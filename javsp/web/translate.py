"""网页翻译接口"""
# 由于翻译服务不走代理，而且需要自己的错误处理机制，因此不通过base.py来管理网络请求
import re
import time
from typing import Union
import uuid
import random
import logging
from pydantic_core import Url
import requests
from hashlib import md5


__all__ = ['translate', 'translate_movie_info']


from javsp.config import BaiduTranslateEngine, BingTranslateEngine, Cfg, ClaudeTranslateEngine, OpenAITranslateEngine
from javsp.datatype import MovieInfo
from javsp.web.base import read_proxy


logger = logging.getLogger(__name__)

# 翻译请求统一超时: 早前全部 requests 调用都没有 timeout, 上游卡住时线程会被
# 无限期占住(Python 无法强杀线程) -> core 的线程池留下不可回收的孤儿线程。
_TIMEOUT = 20
# LLM 引擎(openai/claude)单独的超时: 本地部署的 Ollama 在 CPU 上推理一段剧情
# 简介动辄 30-90 秒, 用 _TIMEOUT 会大量误超时; 云端 LLM 排队也可能超过 20 秒。
_LLM_TIMEOUT = 120
# LLM 输出上限: 原为 1024, JAV 剧情简介译文接近该值时被截断(实测长简介译文
# 约 900-1400 tokens), 提升到 2048。
_LLM_MAX_TOKENS = 2048
# Google 翻译被限流(429)时的最大重试次数: 原为 while 无限循环, 遇到持续 429
# (或响应被劫持成 429) 时等待时间单调递增到数小时, 线程被无限占用。
_GOOGLE_MAX_RETRY = 5

# 翻译目标语种: 界面 code -> (中文名, 百度, Bing, Google, LLM prompt 用语种名)
# 各引擎的语种码互不相同(百度 jp/cht、Bing zh-Hans...), 在此统一收敛成一张表,
# 设置页只暴露界面 code, 引擎层自动换算。用户手写配置里的未知语种回落简体中文。
_LANGUAGES = {
    'zh_CN': ('简体中文', 'zh', 'zh-Hans', 'zh-CN', 'Simplified Chinese (简体中文)'),
    'zh_TW': ('繁体中文', 'cht', 'zh-Hant', 'zh-TW', 'Traditional Chinese (繁體中文)'),
    'en': ('英语', 'en', 'en', 'en', 'English'),
    'ja': ('日语', 'jp', 'ja', 'ja', 'Japanese'),
    'fr': ('法语', 'fra', 'fr', 'fr', 'French'),
    'de': ('德语', 'de', 'de', 'de', 'German'),
    'es': ('西班牙语', 'spa', 'es', 'es', 'Spanish'),
}

def _lang_entry(code: str) -> tuple:
    """把界面语种 code 换成引擎用的元组; 未知 code 回落简体中文"""
    entry = _LANGUAGES.get(code)
    if entry is None:
        logger.warning(f'未知的翻译目标语种 {code!r}, 本次按简体中文处理')
        return _LANGUAGES['zh_CN']
    return entry

# 源文本语种检测: 只用字符集特征(零第三方依赖), 判定粒度到"语族"。
# 拉丁语族内部(英/法/德/西)故意不细分 —— 那需要引入语言模型, 而误判的代价
# (把英文当法文跳过)比多打一次引擎调用更糟。
_KANA_RE = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')      # 平假名 + 片假名
_HANGUL_RE = re.compile(r'[\uac00-\ud7af\u1100-\u11ff]')    # 谚文
_HAN_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')       # 汉字(含扩展A)

def _detect_source_lang(texts) -> str:
    """粗判源文本语族: 'ja'(日文, 含假名) / 'zh'(中文, 含汉字无假名) /
    'hangul'(韩文) / 'latin'(无 CJK 字符) / ''(无有效文本)"""
    if not texts or not str(texts).strip():
        return ''
    s = str(texts)
    if _KANA_RE.search(s):
        return 'ja'
    if _HANGUL_RE.search(s):
        return 'hangul'
    if _HAN_RE.search(s):
        # 含汉字且无假名: 简/繁都归为中文(细分简繁没有意义, 目标无论是
        # zh_CN 还是 zh_TW, 用字统一都由落盘时的繁简归一化负责)
        return 'zh'
    return 'latin'

# 目标语种 -> 视为"同一种语言"因而跳过翻译的源语族集合。
# 只在能可靠判定的组合上启用:
# - 中文目标(zh_CN/zh_TW): 源已是中文则不翻(部分源的标题/简介直接给中文,
#   再喂给翻译引擎只会得到同义改写, 反而破坏与源数据的一致性);
# - 日语目标: 源已含假名则不翻。
# 英/法/德/西目标不启用: 拉丁语族内部无法零依赖区分, 跳过会漏翻。
_SAME_LANG_SKIP = {
    'zh_CN': ('zh',),
    'zh_TW': ('zh',),
    'ja': ('ja',),
}

def _should_skip_translation(texts, to: str) -> bool:
    """源文本与目标语言相同时返回 True(跳过翻译)

    无有效文本也返回 True —— 空字符串没什么可译, 提前返回可省掉一次引擎调用
    (百度/Google 还有 QPS 限制, 空文本也会占用配额)。
    """
    if not texts or not str(texts).strip():
        return True
    src = _detect_source_lang(texts)
    return src in _SAME_LANG_SKIP.get(to, ())

# few-shot 示例: LLM prompt 里用「こんにちは→目标语种」示范期望输出。
# 4B 量级小模型对纯文字指令的语种遵循很弱(实测指定英语仍输出中文), 一个
# 具体示例能显著提升遵循度。
_LANG_EXAMPLES = {
    'zh_CN': '你好',
    'zh_TW': '你好',
    'en': 'Hello',
    'ja': 'こんにちは',
    'fr': 'Bonjour',
    'de': 'Hallo',
    'es': 'Hola',
}


def translate_movie_info(info: MovieInfo):
    """根据配置翻译影片信息"""
    # 翻译目标语种: 设置页可选项(默认简体中文), 旧配置无 language 字段时 pydantic 给默认值
    to = Cfg().translator.language
    # 翻译标题
    if info.title and Cfg().translator.fields.title and info.ori_title is None:
        if _should_skip_translation(info.title, to):
            # 源文本与目标语言相同: 不调用引擎, 也不记 ori_title
            # (否则 NFO 的 <originaltitle> 会变成原文的冗余副本);
            # 简/繁用字差异由落盘时 write_nfo 的归一化统一。
            logger.debug(f'标题与目标语言 {to} 相同, 跳过翻译')
        else:
            result = translate(info.title, Cfg().translator.engine, info.actress, to=to)
            if 'trans' in result:
                info.ori_title = info.title
                info.title = result['trans']
                # 如果有的话，附加断句信息
                if 'orig_break' in result:
                    setattr(info, 'ori_title_break', result['orig_break'])
                if 'trans_break' in result:
                    setattr(info, 'title_break', result['trans_break'])
            else:
                logger.error('翻译标题时出错: ' + result['error'])
                return False
    # 翻译简介
    # ori_plot 守卫: 刮削阶段已预翻译过的影片(info.ori_plot 有值), 落盘时再调
    # 本函数不应把译文当原文二次翻译(译文→译文会累积语种漂移, 简繁混用的源头之一)
    if info.plot and Cfg().translator.fields.plot and getattr(info, 'ori_plot', None) is None:
        if _should_skip_translation(info.plot, to):
            logger.debug(f'简介与目标语言 {to} 相同, 跳过翻译')
        else:
            result = translate(info.plot, Cfg().translator.engine, info.actress, to=to)
            if 'trans' in result:
                # 只有翻译过plot的影片才可能需要ori_plot属性，因此在运行时动态添加，而不添加到类型定义里
                setattr(info, 'ori_plot', info.plot)
                info.plot = result['trans']
            else:
                logger.error('翻译简介时出错: ' + result['error'])
                return False
    return True

def translate(texts, engine: Union[
        BaiduTranslateEngine,
        BingTranslateEngine,
        ClaudeTranslateEngine,
        OpenAITranslateEngine,
        None
    ], actress=[], to=None):
    """
    翻译入口：对错误进行处理并且统一返回格式

    Args:
        to: 目标语种界面 code(zh_CN/zh_TW/en/ja/fr/de/es)，None 时按简体中文。
            各引擎函数内部再用 _lang_entry 换成本引擎的语种码/语种名。
    """
    to = to or 'zh_CN'
    rtn = {}
    err_msg = ''
    # 源文本与目标语言相同(或文本为空)时直接返回原文, 不打引擎。
    # translate_movie_info 有自己的跳过分支(不记 ori_title/ori_plot), 这里兜底
    # 其它调用方, 同时避免空文本占用百度/Google 的 QPS 配额。
    if _should_skip_translation(texts, to):
        logger.debug(f'源文本与目标语言 {to} 相同或为空, 跳过翻译')
        return {'trans': texts}
    if engine.name == 'baidu':
        result = baidu_translate(texts, engine.app_id, engine.api_key, to=to)
        if 'error_code' not in result:
            # 百度翻译的结果中的组表示的是按换行符分隔的不同段落，而不是句子
            paragraphs = [i['dst'] for i in result['trans_result']]
            rtn = {'trans': '\n'.join(paragraphs)}
        else:
            err_msg = "{}: {}: {}".format(engine.name, result['error_code'], result['error_msg'])
    elif engine.name == 'bing':
        # 使用动态词典保护原文中的女优名，防止翻译后认不出来
        for i in actress:
            texts = texts.replace(i, f'<mstrans:dictionary translation="{i}">{i}</mstrans:dictionary>')
        result = bing_translate(texts, api_key=engine.api_key, to=to)
        if 'error' not in result:
            sentLen = result[0]['translations'][0]['sentLen']
            orig_break, trans_break = [], []
            # 对原文进行断句
            remaining = texts
            for i in sentLen['srcSentLen']:
                orig_break.append(remaining[:i])
                remaining = remaining[i:]
            # 对译文进行断句
            remaining = result[0]['translations'][0]['text']
            for i in sentLen['transSentLen']:
                # Bing会在译文的每个句尾添加一个空格，这并不符合中文的标点习惯，所以去掉这个空格
                trans_break.append(remaining[:i].rstrip(' '))
                remaining = remaining[i:]
            trans = ''.join(trans_break)
            rtn = {'trans': trans, 'orig_break': orig_break, 'trans_break': trans_break}
        else:
            err_msg = "{}: {}: {}".format(engine.name, result['error']['code'], result['error']['message'])
    elif engine.name == 'claude':
        try:
            result = claude_translate(texts, engine.api_key, getattr(engine, 'use_proxy', False),
                                      to=to)
            if 'error_code' not in result:
                rtn = {'trans': result}
            else:
                err_msg = "{}: {}: {}".format(engine.name, result['error_code'], result['error_msg'])
        except Exception as e:
            err_msg = "{}: {}: Exception: {}".format(engine.name, -2, repr(e))
    elif engine.name == 'openai':
        try:
            result = openai_translate(texts, engine.url, engine.api_key, engine.model,
                                      getattr(engine, 'use_proxy', False), to=to)
            if 'error_code' not in result:
                rtn = {'trans': result}
            else:
                err_msg = "{}: {}: {}".format(engine.name, result['error_code'], result['error_msg'])
        except Exception as e:
            err_msg = "{}: {}: Exception: {}".format(engine.name, -2, repr(e))
    elif engine.name == 'google':
        try:
            result = google_trans(texts, to=to)
            # 经测试，翻译成功时会带有'sentences'字段；失败时不带，也没有故障码
            if 'sentences' in result:
                # Google会对句子分组，完整的译文需要自行拼接
                orig_break = [i['orig'] for i in result['sentences']]
                trans_break = [i['trans'] for i in result['sentences']]
                trans = ''.join(trans_break)
                rtn = {'trans': trans, 'orig_break': orig_break, 'trans_break': trans_break}
            else:
                err_msg = "{}: {}: {}".format(engine.name, result['error_code'], result['error_msg'])
        except Exception as e:
            err_msg = "{}: {}: Exception: {}".format(engine.name, -2, repr(e))
    else:
        return {'trans': texts}

    if rtn == {}:
        rtn['error'] = err_msg

    return rtn

def baidu_translate(texts, app_id, api_key, to='zh'):
    """使用百度翻译文本（默认翻译为简体中文）

    to 为目标语种界面 code, 内部换算成百度的语种码(zh/cht/en/jp/fra/de/spa)
    """
    api_url = "https://api.fanyi.baidu.com/api/trans/vip/translate"
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    salt = random.randint(0, 0x7FFFFFFF)
    sign_input = app_id + texts + str(salt) + api_key
    sign = md5(sign_input.encode('utf-8')).hexdigest()
    payload = {'appid': app_id, 'q': texts, 'from': 'auto', 'to': _lang_entry(to)[1], 'salt': salt, 'sign': sign}
    # 由于百度标准版限制QPS为1，连续翻译标题和简介会超限，因此需要添加延时
    now = time.perf_counter()
    last_access = getattr(baidu_translate, '_last_access', -1)
    wait = 1.0 - (now - last_access)
    if wait > 0:
        time.sleep(wait)
    r = requests.post(api_url, params=payload, headers=headers, timeout=_TIMEOUT)
    result = r.json()
    baidu_translate._last_access = time.perf_counter()
    return result


def bing_translate(texts, api_key, to='zh-Hans'):
    """使用Bing翻译文本（默认翻译为简体中文）

    to 为目标语种界面 code, 内部换算成 Bing 的语种码(zh-Hans/zh-Hant/en/ja/fr/de/es)
    """
    api_url = "https://api.cognitive.microsofttranslator.com/translate"
    params = {'api-version': '3.0', 'to': _lang_entry(to)[2], 'includeSentenceLength': True}
    headers = {
        'Ocp-Apim-Subscription-Key': api_key,
        'Ocp-Apim-Subscription-Region': 'global',
        'Content-type': 'application/json',
        'X-ClientTraceId': str(uuid.uuid4())
    }
    body = [{'text': texts}]
    r = requests.post(api_url, params=params, headers=headers, json=body, timeout=_TIMEOUT)
    result = r.json()
    return result


_google_trans_wait = 60
def google_trans(texts, to='zh_CN'):
    """使用Google翻译文本（默认翻译为简体中文）

    to 为目标语种界面 code, 内部换算成 Google 的语种码(zh-CN/zh-TW/en/ja/fr/de/es)
    """
    # API: https://www.jianshu.com/p/ce35d89c25c3
    # client参数的选择: https://github.com/lmk123/crx-selection-translate/issues/223#issue-184432017
    global _google_trans_wait
    # texts 是影片标题/简介(来自远端站点的不可信内容), 必须做 URL 编码,
    # 否则其中的 & ? # 会被当作查询参数分隔符 → 可注入额外参数
    from urllib.parse import quote
    url = ("https://translate.google.com.hk/translate_a/single?client=gtx&dt=t&dj=1"
           f"&ie=UTF-8&sl=auto&tl={quote(_lang_entry(to)[3], safe='')}&q={quote(texts, safe='')}")
    proxies = read_proxy()
    r = requests.get(url, proxies=proxies, timeout=_TIMEOUT)
    retry = 0
    while r.status_code == 429 and retry < _GOOGLE_MAX_RETRY:
        retry += 1
        logger.warning(f"HTTP {r.status_code}: {r.reason}: Google翻译请求超限，将等待{_google_trans_wait}秒后重试({retry}/{_GOOGLE_MAX_RETRY})")
        time.sleep(_google_trans_wait)
        r = requests.get(url, proxies=proxies, timeout=_TIMEOUT)
        if r.status_code == 429:
            _google_trans_wait += random.randint(60, 90)
    if r.status_code == 429:
        logger.error(f"Google翻译连续{_GOOGLE_MAX_RETRY}次被限流, 放弃本次翻译")
    if r.status_code == 200:
        result = r.json()
    else:
        result = {'error_code': r.status_code, 'error_msg': r.reason}
    time.sleep(4) # Google翻译的API有QPS限制，因此需要等待一段时间
    return result

def _llm_translate_prompt(to="zh_CN"):
    """LLM 翻译引擎(claude/openai)共用的 system prompt

    三类约束:
    1. 语种一致性(短文本的最大痛点): 本地小模型(实测 translategemma:4b 对日文
       标题即使指定英语也稳定输出中文) 对"目标语种"的遵循很弱。这里把语种指令
       前置, 并附一个 few-shot 示例(输入日语→输出目标语种), 实测能显著提升
       4B 量级模型的遵循度; 配合 temperature=0 保证重复请求结果稳定。
    2. JAV 领域保护: 女优名、番号(如 ABC-123)、厂商/系列名等专有名词保持不译——
       通用机翻和未加约束的 LLM 经常把这些译成似是而非的中文, 导致 NFO 与文件名
       里的番号认不回去。
    3. 输出纯净: 只返回译文, 不加任何额外内容。
    """
    entry = _lang_entry(to)
    example = _LANG_EXAMPLES.get(to, 'Hello')
    return (
        f"Translate the following Japanese text into {entry[4]}. "
        f"You MUST write the entire translation in {entry[4]} only — for example, "
        f"translate \"こんにちは\" as \"{example}\". "
        "Do not mix in Traditional Chinese characters when the target is Simplified "
        "Chinese (or vice versa), and do not leave untranslated Japanese fragments "
        "unless they are proper nouns. "
        "leave actress names, product codes like ABC-123, studio/series names and other "
        "proper nouns untranslated; do not translate text that is not Japanese; "
        "reply with the translated text only, do not add any content that is not in the "
        "original."
    )


def claude_translate(texts, api_key, use_proxy=False, to="zh_CN"):
    """使用Claude翻译文本（默认翻译为简体中文）

    use_proxy: 是否走本项目通用代理(network.proxy_server)。国内访问 Anthropic
    需要代理; 而本模块的设计是翻译服务默认不走代理, 故显式开关。
    """
    api_url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "context-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    proxies = read_proxy() if use_proxy else None
    data = {
        "model": "claude-3-haiku-20240307",
        "system": _llm_translate_prompt(to),
        "max_tokens": _LLM_MAX_TOKENS,
        "messages": [{"role": "user", "content": texts}],
    }
    # use_proxy=False(本地/局域网部署)时用 Session.trust_env=False 彻底忽略环境变量
    # 里的代理: requests 默认会读取 HTTP_PROXY/HTTPS_PROXY, 用户本机常开 Clash 等
    # 代理软件, 连 127.0.0.1 Ollama 的请求也会被丢给代理 -> 连接超时(实测复现)。
    # use_proxy=True 时显式 proxies 优先于环境变量(requests 的 setdefault 语义)。
    with requests.Session() as s:
        s.trust_env = bool(use_proxy)
        r = s.post(api_url, headers=headers, json=data, proxies=proxies, timeout=_LLM_TIMEOUT)
    if r.status_code == 200:
        result = r.json().get("content", [{}])[0].get("text", "").strip()
    else:
        try:
            msg = r.json().get("error", {}).get("message", r.reason)
        except Exception:
            msg = r.reason
        result = {
            "error_code": r.status_code,
            "error_msg": msg,
        }
    return result

def openai_translate(texts, url: Url, api_key: str, model: str, use_proxy=False, to="zh_CN"):
    """使用 OpenAI 翻译文本（默认翻译为简体中文）

    url 可为任何 OpenAI 兼容端点: 官方 API、Groq/OpenRouter/SiliconFlow 等云端源,
    或本地部署的 Ollama/LM Studio(如 http://192.168.x.x:11434/v1/chat/completions)。
    use_proxy: 是否走本项目通用代理; 本地地址应保持 False。
    """
    api_url = str(url)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    proxies = read_proxy() if use_proxy else None
    data = {
         "messages": [
           {
             "role": "system",
             "content": _llm_translate_prompt(to),
           },
           {
             "role": "user",
             "content": texts
           }
         ],
         "model": model,
         "temperature": 0,
         "max_tokens": _LLM_MAX_TOKENS,
    }
    # 同 claude_translate: use_proxy=False 时忽略环境变量代理, 保证本地/局域网
    # Ollama 不被用户本机的代理软件拦截
    with requests.Session() as s:
        s.trust_env = bool(use_proxy)
        r = s.post(api_url, headers=headers, json=data, proxies=proxies, timeout=_LLM_TIMEOUT)
    if r.status_code == 200:
        if 'error' in r.json():
            result = {
                "error_code": r.status_code,
                "error_msg": r.json().get("error", {}).get("message", ""),
            }
        else:
            result = r.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    else:
        try:
            msg = r.json().get("error", {}).get("message", r.reason)
        except Exception:
            msg = r.reason
        result = {
            "error_code": r.status_code,
            "error_msg": msg,
        }
    return result
