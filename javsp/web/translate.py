"""网页翻译接口"""
# 翻译服务有自己的错误处理机制(各引擎的错误形态完全不同), 因此不通过 base.py 的
# 统一请求封装来管理; 代理则由用户在设置页显式开关(见 _resolve_use_proxy)。
import re
import threading
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
from javsp import transcache


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

# 百度翻译错误码 -> 中文可操作提示。
# 官方返回的 error_msg 是英文(如 "Invalid Access Limit"), 用户在界面上看到
# "54003: Invalid Access Limit" 根本判断不出是限流还是欠费, 只能反馈"翻译失败"。
_BAIDU_ERRORS = {
    '52001': '请求超时, 请重试',
    '52002': '百度服务端系统错误, 请稍后重试',
    '52003': '未授权用户: APP ID / 密钥有误, 或未开通该服务',
    '54000': '必填参数为空',
    '54001': '签名错误: 请检查 APP ID 与密钥是否匹配',
    '54003': '访问频率受限(QPS 超限): 标准版额度为 1 QPS, 可在配置里调低 qps 或升级为高级版',
    '54004': '账户余额不足, 请充值',
    '54005': '长请求过于频繁, 请稍后再试',
    '58000': '客户端 IP 非法: 请在百度开放平台把当前出口 IP 加入白名单',
    '58001': '译文语言方向不支持',
    '58002': '服务已关闭, 请在百度开放平台开启',
    '90107': '认证未通过或未开通服务',
}

# 引擎级 QPS 节流状态: 按引擎名分别记录"下一个可用时间槽"。
# 旧实现把上次访问时间挂在函数属性上, 且在**请求后**才更新: 多线程刮削时多个
# 线程同时读到同一个旧值并一起放行, 真实 QPS 远高于配置值, 反而更容易触发
# 百度的 54003 / Google 的 429。改成在锁内"预约并推进"下一个可用时间槽。
_throttle_lock = threading.Lock()
_throttle_next_slot = {}

# 各引擎单次请求的文本上限(按 UTF-8 字节计), 超过则自动分段后逐段翻译再拼接。
#   baidu : 官方限制单次请求 6000 字节
#   google: 文本走 GET 查询参数, quote(safe='') 后膨胀近 9 倍(实测 6000 字节 ->
#           18100 字符, 远超多数中间设备 8KB 的 URL 上限, 会被截断或直接 414)。
#           取 1800 字节, 编码后约 5.4K 字符, 留足余量。
#   bing  : Azure 单次上限较大, 取 15000 字节留足余量
#   LLM(claude/openai): 上下文足够长, 不分段
_ENGINE_MAX_BYTES = {'baidu': 6000, 'google': 1800, 'bing': 15000}

# Bing 动态词典标记(用于保护女优名不被翻译)。断句还原时需要把它剥掉。
_DICT_MARK_RE = re.compile(r'<mstrans:dictionary translation="(.*?)">.*?</mstrans:dictionary>', re.S)

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

def _resolve_use_proxy(engine, override=None) -> bool:
    """确定本次翻译是否走通用代理(network.proxy_server)

    优先级: override(测试接口传入的界面临时值) > translator.use_proxy(通用开关)
    > engine.use_proxy(旧配置兼容)。

    各引擎**一视同仁**, 不再保留"Google 无条件走代理"的特例 —— 是否走代理完全
    由用户在界面上手动选择。开关为 None(旧配置从未设置过)时回落到引擎自身的
    use_proxy 字段, 让老用户的 claude/openai 配置平滑迁移; google/baidu/bing
    没有该字段, 回落 False(即: 不勾选就不走代理)。
    """
    if override is not None:
        return bool(override)
    top = getattr(Cfg().translator, 'use_proxy', None)
    if top is not None:
        return bool(top)
    # 旧配置里 claude/openai 的 engine.use_proxy 仍生效; 其余引擎无此字段 -> False
    return bool(getattr(engine, 'use_proxy', False))


# 错误信息里出现 URL 时, 查询串往往带着凭据: 百度把 appid/sign 放在 query,
# 自建 OpenAI 兼容端也可能把 key 拼在 URL 上。requests 的异常 repr 会原样带出
# 完整 URL —— 这些 err_msg 会写进日志、回显到界面、被用户贴到 issue 里。
_URL_QUERY_RE = re.compile(r'(https?://[^\s\'"]+)\?[^\s\'"]*')
# 异常 repr 的长度上限: 既能看清类型与原因, 又不会把整页 HTML 灌进日志
_ERR_REPR_LIMIT = 300


def redact_url(s: str) -> str:
    """抹掉文本中所有 http(s) URL 的查询串(凭据/签名都在这里)

    上层(server 的接口回显等)复用同一套规则, 避免各处各写一份正则而漏掉某个出口。
    """
    return _URL_QUERY_RE.sub(r'\1?<query omitted>', s)


def _safe_exc(e) -> str:
    """异常 -> 可安全落盘与回显的字符串: 去查询串 + 截断"""
    return redact_url(repr(e))[:_ERR_REPR_LIMIT]


def _xml_attr(s: str) -> str:
    """XML 属性值转义: 女优名等外部文本拼进属性值前必须转义, 否则其中的
    & < > " 会破坏 Bing 动态词典标记的 XML 结构(属注入面, 不只是显示问题)"""
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
             .replace('"', '&quot;').replace("'", '&apos;'))


def _xml_unattr(s: str) -> str:
    """_xml_attr 的逆变换(把标记里保存的原文还原出来)"""
    return (s.replace('&apos;', "'").replace('&quot;', '"')
             .replace('&gt;', '>').replace('&lt;', '<').replace('&amp;', '&'))


def _unmark_dictionary(s: str) -> str:
    """把 Bing 动态词典标记还原成干净原文片段

    sentLen 是按**替换后**的文本给出的句子长度, 所以断句得到的原文片段里带着
    <mstrans:dictionary ...> 标记。旧代码直接把它写进 orig_break, 导致 NFO 里的
    原文断句被 XML 标记污染。这里剥掉标记, 还原成原始女优名。
    """
    return _DICT_MARK_RE.sub(lambda m: _xml_unattr(m.group(1)), s)


def _dict_mark(name: str) -> str:
    """把单个名字包成 Bing 动态词典标记(属性值已转义)"""
    esc = _xml_attr(name)
    return f'<mstrans:dictionary translation="{esc}">{esc}</mstrans:dictionary>'


def _mark_actress_names(text: str, actress) -> str:
    """给文本里的女优名套上 Bing 动态词典标记, 防止被译掉

    ⚠️ 不能写成 for 循环逐个 str.replace —— 女优名之间存在子串关系时(如
    「佐藤」与「佐藤美咲」), 先处理短名会把长名切断, 后处理长名就已无处可匹配;
    反过来先处理长名则会在已生成的标记内部再替换一次, 得到**嵌套标记**
    (实测 ['佐藤美咲','佐藤'] 顺序下产出 1 个嵌套标记), Bing 拿到畸形 XML 后
    要么忽略词典要么直接报错。

    这里改成: 名字按长度降序排(保证正则交替时长名优先命中), 再用一次 re.sub
    扫描全文。单次扫描天然不会在替换结果里二次匹配, 不存在嵌套。
    """
    names = sorted({i for i in (actress or []) if i and i in text},
                   key=len, reverse=True)
    if not names:
        return text
    pattern = re.compile('|'.join(re.escape(n) for n in names))
    return pattern.sub(lambda m: _dict_mark(m.group(0)), text)


def _throttle(name: str, interval: float) -> None:
    """按引擎名节流: 在锁内预约并推进下一个可用时间槽

    进入请求前就把时间槽往后推, 后续线程自然排到后面的槽位 —— 这样并发刮削时
    真实 QPS 不会超过配置值(旧实现在请求后才更新时间戳, 并发下形同虚设)。

    interval 会被夹在 [0.05, 10] 秒: 0.05 对应 20 QPS(百度配置的上限), 10 秒
    防止配置项被误填成天文数字而把线程挂死。
    """
    try:
        interval = float(interval)
    except (TypeError, ValueError):
        interval = 1.0
    interval = min(max(interval, 0.05), 10.0)
    with _throttle_lock:
        now = time.perf_counter()
        nxt = _throttle_next_slot.get(name, 0.0)
        if nxt <= now:
            _throttle_next_slot[name] = now + interval
            return
        wait = nxt - now
        _throttle_next_slot[name] = nxt + interval
    time.sleep(wait)


def _throttle_baidu(qps: float) -> None:
    """百度按配置 QPS 节流(qps 已在 config 侧校验, 这里再夹一次防误配)"""
    try:
        qps = float(qps)
    except (TypeError, ValueError):
        qps = 1.0
    _throttle('baidu', 1.0 / min(max(qps, 0.1), 20.0))


def _split_text(text: str, max_bytes: int) -> list:
    """按字节上限把长文本切成若干段(尽量在句读处断开)

    各引擎对单次请求的文本长度都有硬上限(百度 6000 字节、Google 走 GET 查询
    参数有 URL 长度风险), 超限时整条请求直接失败。切分优先在句号/换行处断开,
    单句本身就超长时才按字符硬切, 以尽量保留语义完整。
    """
    if not max_bytes or len(text.encode('utf-8')) <= max_bytes:
        return [text]
    chunks, cur = [], ''
    for piece in re.split(r'(?<=[。．.!！?？\n])', text):
        if len(piece.encode('utf-8')) > max_bytes:
            # 单句就超限: 按字符硬切
            if cur:
                chunks.append(cur)
                cur = ''
            buf = ''
            for ch in piece:
                if len((buf + ch).encode('utf-8')) > max_bytes:
                    chunks.append(buf)
                    buf = ch
                else:
                    buf += ch
            if buf:
                chunks.append(buf)
            continue
        if len((cur + piece).encode('utf-8')) > max_bytes:
            if cur:
                chunks.append(cur)
            cur = piece
        else:
            cur += piece
    if cur:
        chunks.append(cur)
    logger.info('翻译文本超长(%d 字节 > %d), 已自动分为 %d 段',
                len(text.encode('utf-8')), max_bytes, len(chunks))
    return chunks


def _engine_identity(engine, name, to):
    """返回 (缓存用引擎标识, LLM system prompt)

    引擎标识含模型名 —— 换了模型就自动 miss, 不会复用旧译文; LLM 的 prompt
    也参与缓存 key 计算(见 transcache.make_key), 改提示词同样自动失效。
    """
    if name in ('claude', 'openai'):
        return f'{name}|{getattr(engine, "model", "") or ""}', _llm_translate_prompt(to)
    return f'{name}|', ''


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
    ], actress=[], to=None, use_proxy=None):
    """
    翻译入口：对错误进行处理并且统一返回格式

    Args:
        to: 目标语种界面 code(zh_CN/zh_TW/en/ja/fr/de/es)，None 时按简体中文。
            各引擎函数内部再用 _lang_entry 换成本引擎的语种码/语种名。
        use_proxy: 显式指定本次是否走通用代理; None 表示由通用开关
            (translator.use_proxy)或旧配置的 engine.use_proxy 决定。设置页的
            「测试」按钮会传入界面上的临时值, 用于不保存即验证配置是否可用。
    """
    to = to or 'zh_CN'
    # 源文本与目标语言相同(或文本为空)时直接返回原文, 不打引擎。
    # translate_movie_info 有自己的跳过分支(不记 ori_title/ori_plot), 这里兜底
    # 其它调用方, 同时避免空文本占用百度/Google 的 QPS 配额。
    if _should_skip_translation(texts, to):
        logger.debug(f'源文本与目标语言 {to} 相同或为空, 跳过翻译')
        return {'trans': texts}
    # 是否走代理对所有引擎一视同仁, 统一由通用开关决定(见 _resolve_use_proxy)
    proxy = _resolve_use_proxy(engine, override=use_proxy)
    name = getattr(engine, 'name', None)
    if name not in ('baidu', 'bing', 'claude', 'openai', 'google'):
        return {'trans': texts}
    # 缓存: 命中即返回。换引擎/模型/语种/原文/prompt 任一变化都会自动 miss。
    engine_id, prompt = _engine_identity(engine, name, to)
    key = transcache.make_key(engine_id, to, prompt, texts)
    cached = transcache.get(key)
    if cached is not None:
        return cached
    rtn = _translate_all(texts, engine, actress, to, proxy, name)
    # 只缓存成功结果: 一次网络抖动不该被长期记住
    if 'trans' in rtn:
        transcache.put(key, rtn, engine_id)
    return rtn


# 分段拼接时各引擎的分隔符(默认空串)。
#   baidu: 百度的 trans_result 是按换行分隔的**段落**, dst 里不带换行(由上层用
#          '\n'.join 重建)。分段边界通常落在句号上(段末没有换行), 若继续用空串
#          拼接, 原文里"段A\n段B"就会塌成"段A段B" —— NFO 的多行简介会并成一行。
#   google/bing: 译文按句子拼接的连续文本, 保持空串。
_JOIN_SEP = {'baidu': '\n'}


def _translate_all(texts, engine, actress, to, proxy, name):
    """执行翻译: 超过引擎单次上限时自动分段, 逐段翻译后按序拼接"""
    chunks = _split_text(texts, _ENGINE_MAX_BYTES.get(name))
    if len(chunks) == 1:
        return _translate_chunk(chunks[0], engine, actress, to, proxy, name)
    trans, orig_break, trans_break = [], [], []
    for chunk in chunks:
        r = _translate_chunk(chunk, engine, actress, to, proxy, name)
        if 'trans' not in r:
            # 任一段失败即整体失败: 避免 NFO 里出现半译文半原文的残次内容
            return r
        trans.append(r['trans'])
        orig_break.extend(r.get('orig_break', []))
        trans_break.extend(r.get('trans_break', []))
    rtn = {'trans': _JOIN_SEP.get(name, '').join(trans)}
    if orig_break:
        rtn['orig_break'] = orig_break
    if trans_break:
        rtn['trans_break'] = trans_break
    return rtn


def _translate_chunk(texts, engine, actress, to, proxy, name):
    """单段文本的引擎调用与错误处理

    ⚠️ 每个分支都必须有异常兜底: 百度/Bing 旧实现直接裸调 requests, 网络异常会
    一路冒泡到 translate_movie_info —— 标题翻译抛异常后简介根本不会执行, 整部
    影片的翻译全废, 而日志只剩一坨 requests 的异常 repr。
    """
    rtn, err_msg = {}, ''
    if name == 'baidu':
        try:
            result = baidu_translate(texts, engine.app_id, engine.api_key, to=to,
                                     use_proxy=proxy, qps=getattr(engine, 'qps', 1.0))
        except Exception as e:
            err_msg = "{}: {}: Exception: {}".format(name, -2, _safe_exc(e))
        else:
            if isinstance(result, dict) and 'error_code' not in result:
                # 百度翻译的结果中的组表示的是按换行符分隔的不同段落，而不是句子。
                # 必须要求 items 非空: all([]) 恒为真, 缺 trans_result 时会静默
                # 产出空译文 —— 那会把 NFO 的标题/简介清空, 比报错更糟。
                items = result.get('trans_result') or []
                if isinstance(items, list) and items and all(isinstance(i, dict) and 'dst' in i for i in items):
                    rtn = {'trans': '\n'.join(i['dst'] for i in items)}
                else:
                    err_msg = "{}: {}: 响应结构异常(缺少 trans_result): {}".format(
                        name, -3, repr(result)[:200])
            elif isinstance(result, dict):
                err_msg = "{}: {}: {}".format(name, result.get('error_code', '未知'),
                                              result.get('error_msg', ''))
            else:
                err_msg = "{}: {}: 响应结构异常: {}".format(name, -3, repr(result)[:200])
    elif name == 'bing':
        # 使用动态词典保护原文中的女优名，防止翻译后认不出来。
        # 女优名是来自远端站点的不可信文本, 拼进 XML 属性值前必须转义
        # (转义与防嵌套都在 _mark_actress_names 内完成)。
        marked = _mark_actress_names(texts, actress)
        try:
            result = bing_translate(marked, api_key=engine.api_key, to=to, use_proxy=proxy,
                                    region=getattr(engine, 'region', 'global') or 'global')
        except Exception as e:
            err_msg = "{}: {}: Exception: {}".format(name, -2, _safe_exc(e))
        else:
            # 成功返回 list; 失败返回带 'error' 的 dict。空 list 也要防 ——
            # 旧代码直接 result[0] 会抛 IndexError。
            first = result[0] if isinstance(result, list) and result else None
            trans_list = (first or {}).get('translations') or [{}]
            trans0 = trans_list[0] if isinstance(trans_list, list) and trans_list else {}
            sent_len = trans0.get('sentLen') or {}
            if first and sent_len:
                orig_break, trans_break = [], []
                # 对原文进行断句
                remaining = marked
                for n in sent_len.get('srcSentLen', []):
                    orig_break.append(_unmark_dictionary(remaining[:n]))
                    remaining = remaining[n:]
                # 对译文进行断句
                remaining = trans0.get('text', '')
                for n in sent_len.get('transSentLen', []):
                    # Bing会在译文的每个句尾添加一个空格，这并不符合中文的标点习惯，所以去掉这个空格
                    trans_break.append(remaining[:n].rstrip(' '))
                    remaining = remaining[n:]
                rtn = {'trans': ''.join(trans_break), 'orig_break': orig_break,
                       'trans_break': trans_break}
            elif isinstance(result, dict) and 'error' in result:
                err = result['error']
                err_msg = "{}: {}: {}".format(name, err.get('code', '未知'),
                                              err.get('message', ''))
            else:
                err_msg = "{}: {}: 响应结构异常(空结果或非预期格式): {}".format(
                    name, -3, repr(result)[:200])
    elif name == 'claude':
        try:
            result = claude_translate(texts, engine.api_key, proxy, to=to,
                                      model=getattr(engine, 'model', None))
            if isinstance(result, str):
                rtn = {'trans': result}
            else:
                err_msg = "{}: {}: {}".format(name, result.get('error_code', '未知'),
                                              result.get('error_msg', ''))
        except Exception as e:
            err_msg = "{}: {}: Exception: {}".format(name, -2, _safe_exc(e))
    elif name == 'openai':
        try:
            result = openai_translate(texts, engine.url, engine.api_key, engine.model,
                                      proxy, to=to)
            if isinstance(result, str):
                rtn = {'trans': result}
            else:
                err_msg = "{}: {}: {}".format(name, result.get('error_code', '未知'),
                                              result.get('error_msg', ''))
        except Exception as e:
            err_msg = "{}: {}: Exception: {}".format(name, -2, _safe_exc(e))
    elif name == 'google':
        try:
            result = google_trans(texts, to=to, use_proxy=proxy)
            # 经测试，翻译成功时会带有'sentences'字段；失败时不带
            # isinstance 守卫: 若 Google 退化成返回 list(旧版无 dj=1 的形态),
            # "'sentences' in result" 会退化成逐元素比较而误判为成功
            if isinstance(result, dict) and 'sentences' in result:
                # Google会对句子分组，完整的译文需要自行拼接
                orig_break = [i['orig'] for i in result['sentences']]
                trans_break = [i['trans'] for i in result['sentences']]
                trans = ''.join(trans_break)
                rtn = {'trans': trans, 'orig_break': orig_break, 'trans_break': trans_break}
            else:
                # 失败详情由 google_trans 统一写入 error_code/error_msg。
                # 旧代码直接下标 result['error_code'], 一旦响应结构意外就抛
                # KeyError, 被下面的 except 兜成
                # "Exception: KeyError('error_code')" —— 把真实原因完全盖掉,
                # 用户和日志都看不出到底是网络问题还是限流。改用 .get() 取值。
                if isinstance(result, dict):
                    code = result.get('error_code', '未知')
                    msg = result.get('error_msg', repr(result)[:200])
                else:
                    code, msg = '未知', repr(result)[:200]
                err_msg = "{}: {}: {}".format(name, code, msg)
        except Exception as e:
            err_msg = "{}: {}: Exception: {}".format(name, -2, _safe_exc(e))

    if rtn == {}:
        rtn['error'] = err_msg

    return rtn

def baidu_translate(texts, app_id, api_key, to='zh', use_proxy=False, qps=1.0):
    """使用百度翻译文本（默认翻译为简体中文）

    to 为目标语种界面 code, 内部换算成百度的语种码(zh/cht/en/jp/fra/de/spa)
    use_proxy: 是否走通用代理(此前百度引擎完全不支持代理配置)
    qps: 每秒请求上限(标准版 1, 高级版可达 10), 由配置 cache...engine.qps 传入

    返回: 成功为百度原始 dict(含 'trans_result'); 失败为
    {'error_code': ..., 'error_msg': ...}, error_code 取值:
      -1  网络层失败(连不上/代理不通/超时)
      -3  HTTP 200 但响应不是 JSON 或结构异常
      >0  百度业务错误码(已按 _BAIDU_ERRORS 换成中文提示)
    """
    api_url = "https://api.fanyi.baidu.com/api/trans/vip/translate"
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    salt = random.randint(0, 0x7FFFFFFF)
    sign_input = app_id + texts + str(salt) + api_key
    sign = md5(sign_input.encode('utf-8')).hexdigest()
    payload = {'appid': app_id, 'q': texts, 'from': 'auto', 'to': _lang_entry(to)[1], 'salt': salt, 'sign': sign}
    # 百度标准版限制 QPS 为 1, 连续翻译标题和简介会超限。按配置节流(线程安全)。
    _throttle_baidu(qps)
    proxies = read_proxy() if use_proxy else None
    try:
        r = requests.post(api_url, params=payload, headers=headers, proxies=proxies, timeout=_TIMEOUT)
    except requests.exceptions.RequestException as e:
        return {'error_code': -1,
                'error_msg': f'无法连接百度翻译({type(e).__name__})'}
    try:
        result = r.json()
    except ValueError:
        return {'error_code': -3,
                'error_msg': f'百度翻译返回非JSON响应: {r.text[:200]}'}
    if not isinstance(result, dict):
        return {'error_code': -3, 'error_msg': f'百度翻译响应结构异常: {repr(result)[:200]}'}
    # 官方 error_msg 是英文且不含排查方向, 换成中文可操作提示
    code = result.get('error_code')
    if code is not None:
        tip = _BAIDU_ERRORS.get(str(code))
        if tip:
            result['error_msg'] = tip
    return result


def bing_translate(texts, api_key, to='zh-Hans', use_proxy=False, region='global'):
    """使用Bing翻译文本（默认翻译为简体中文）

    to 为目标语种界面 code, 内部换算成 Bing 的语种码(zh-Hans/zh-Hant/en/ja/fr/de/es)
    use_proxy: 是否走通用代理(此前 Bing 引擎完全不支持代理配置)
    region: Azure 资源所在区域。多服务/全球资源用 global; 单服务资源(如 eastasia)
        必须填实际区域, 否则返回 401001 且用户无从判断原因。

    返回: 成功为 Bing 原始 list; 失败为 {'error': {'code':..., 'message':...}}
    """
    api_url = "https://api.cognitive.microsofttranslator.com/translate"
    params = {'api-version': '3.0', 'to': _lang_entry(to)[2], 'includeSentenceLength': True}
    headers = {
        'Ocp-Apim-Subscription-Key': api_key,
        'Ocp-Apim-Subscription-Region': region or 'global',
        'Content-type': 'application/json',
        'X-ClientTraceId': str(uuid.uuid4())
    }
    body = [{'text': texts}]
    proxies = read_proxy() if use_proxy else None
    try:
        r = requests.post(api_url, params=params, headers=headers, json=body,
                          proxies=proxies, timeout=_TIMEOUT)
    except requests.exceptions.RequestException as e:
        return {'error': {'code': -1,
                          'message': f'无法连接 Bing/Azure 翻译({type(e).__name__})'}}
    try:
        return r.json()
    except ValueError:
        return {'error': {'code': -3,
                          'message': f'Bing返回非JSON响应: {r.text[:200]}'}}


# Google 翻译被限流(429)时的退避基准与上限(秒)。
# ⚠️ 旧实现用模块级全局变量 _google_trans_wait, 每次 429 后累加且**从不重置**:
# 等待时间会跨调用永久累积(一次限流后, 后续所有翻译都从最长的等待起步, 最坏
# 单次调用累计等待约 17 分钟) → 表现为"翻译越来越慢直至卡死", 而用户端只看到
# 一个无法诊断的失败。改为每次调用内的局部退避: 指数增长并夹在上限内。
_GOOGLE_BACKOFF_BASE = 5
_GOOGLE_BACKOFF_CAP = 60
# 单次调用内 429 退避的**累计**等待预算(秒)。旧实现只限制次数: 5 次退避的等待
# 为 5+10+20+40+60=135 秒, 而每段长文本都要走一遍 —— 一部影片的简介分 4 段就是
# 9 分钟, 线程被长期占用, 前端表现为"卡住不动"。超过预算即放弃本次翻译, 把
# 失败快速交回上层去重试或降级。
_GOOGLE_BACKOFF_BUDGET = 120
# 成功路径的引擎级节流间隔(秒)。
# 旧实现是在**每次请求成功后固定 sleep(4)**, 有两个问题: ①它是调用内的 sleep,
# 并发刮削时各线程各睡各的, 对服务端而言真实 QPS 仍可能超标; ②长文本分段后
# 每段都要睡 4 秒(4 段 = 16 秒), 而失败路径又完全不限速。改成与百度相同的
# "预约时间槽"节流: 所有线程共享同一个槽位, 且成败一视同仁。
# 取 2 秒而非 4 秒: 免费接口的实测限流阈值远低于 0.25 QPS, 2 秒的间隔已足够
# 规避绝大多数 429, 同时把长文本的整体耗时压到原来的一半。
_GOOGLE_MIN_INTERVAL = 2.0

def google_trans(texts, to='zh_CN', use_proxy=True):
    """使用Google翻译文本（默认翻译为简体中文）

    to 为目标语种界面 code, 内部换算成 Google 的语种码(zh-CN/zh-TW/en/ja/fr/de/es)
    use_proxy: 是否走通用代理。与其它引擎一视同仁, 默认 False —— 是否走代理由
        用户在界面上手动选择(不再保留"Google 无条件走代理"的特例)。实际取值
        由 _resolve_use_proxy 统一决定。

    返回: 成功为 Google 原始 dict(含 'sentences'); 失败为
    {'error_code': ..., 'error_msg': ...}, error_code 取值:
      -1  网络层失败(连不上/代理不通/超时) —— 国内直连 Google 最常见的原因
      -3  HTTP 200 但响应不是 JSON(被拦截页或运营商劫持)
      >0  真实 HTTP 状态码(如 429 限流)
    """
    # API: https://www.jianshu.com/p/ce35d89c25c3
    # client参数的选择: https://github.com/lmk123/crx-selection-translate/issues/223#issue-184432017
    # texts 是影片标题/简介(来自远端站点的不可信内容), 必须做 URL 编码,
    # 否则其中的 & ? # 会被当作查询参数分隔符 → 可注入额外参数
    from urllib.parse import quote
    url = ("https://translate.google.com.hk/translate_a/single?client=gtx&dt=t&dj=1"
           f"&ie=UTF-8&sl=auto&tl={quote(_lang_entry(to)[3], safe='')}&q={quote(texts, safe='')}")
    proxies = read_proxy() if use_proxy else None

    def _network_error(e):
        """网络层异常 -> 可读错误。

        国内用户用 Google 引擎失败绝大多数落在这里(直连不通)。旧代码把
        requests 的异常 repr 整坨抛给上层, 用户既看不懂也不知道要去配代理,
        只能反馈"翻译失败"。这里压缩成异常类型并给出可操作的排查方向。
        """
        hint = ''
        if isinstance(e, (requests.exceptions.ProxyError,
                          requests.exceptions.ConnectTimeout)):
            hint = '；国内直连 Google 通常需要配置 network.proxy_server 代理'
        return {'error_code': -1,
                'error_msg': f'无法连接 Google 翻译({type(e).__name__}){hint}'}

    _throttle('google', _GOOGLE_MIN_INTERVAL)
    try:
        r = requests.get(url, proxies=proxies, timeout=_TIMEOUT)
    except requests.exceptions.RequestException as e:
        return _network_error(e)
    wait = _GOOGLE_BACKOFF_BASE
    retry = 0
    waited = 0.0
    while r.status_code == 429 and retry < _GOOGLE_MAX_RETRY:
        if waited + wait > _GOOGLE_BACKOFF_BUDGET:
            logger.error(f"Google翻译退避累计将超过{_GOOGLE_BACKOFF_BUDGET}秒预算, "
                         f"停止重试(已重试{retry}次)")
            break
        retry += 1
        logger.warning(f"HTTP {r.status_code}: {r.reason}: Google翻译请求超限，将等待{wait}秒后重试({retry}/{_GOOGLE_MAX_RETRY})")
        time.sleep(wait)
        waited += wait
        wait = min(wait * 2, _GOOGLE_BACKOFF_CAP)
        try:
            r = requests.get(url, proxies=proxies, timeout=_TIMEOUT)
        except requests.exceptions.RequestException as e:
            return _network_error(e)
    if r.status_code == 429:
        logger.error(f"Google翻译连续{retry}次被限流, 放弃本次翻译")
    if r.status_code == 200:
        # 200 也可能不是 JSON(被拦截页/运营商劫持会返回 HTML)。直接 .json()
        # 会抛 ValueError 并被上层当成"未知异常", 反而把真实响应内容盖掉。
        try:
            result = r.json()
        except ValueError:
            snippet = r.text[:200].replace('\n', ' ')
            ctype = r.headers.get('content-type')
            result = {'error_code': -3,
                      'error_msg': f'Google翻译返回非JSON响应(content-type={ctype}): {snippet}'}
    else:
        result = {'error_code': r.status_code, 'error_msg': r.reason}
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


# Claude 默认模型(与 config.ClaudeTranslateEngine.model 保持一致)。
# ⚠️ 旧值 claude-3-haiku-20240307 已被 Anthropic **硬退役**: 自 2026-04-20 起
# 对该模型 ID 的请求直接报错, 官方推荐迁移到 Haiku 4.5, 故默认值换成在役型号。
_CLAUDE_DEFAULT_MODEL = 'claude-haiku-4-5-20251001'

def claude_translate(texts, api_key, use_proxy=False, to="zh_CN", model=None):
    """使用Claude翻译文本（默认翻译为简体中文）

    use_proxy: 是否走本项目通用代理(network.proxy_server)。国内访问 Anthropic
    需要代理; 而本模块的设计是翻译服务默认不走代理, 故显式开关。
    model: 模型名; None 时用 _CLAUDE_DEFAULT_MODEL。
    """
    api_url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        # 旧代码这里写的是 "context-type"(拼写错误), 一个永远无效的请求头;
        # requests 用 json= 会自带正确的 Content-Type, 故直接删掉。
        "anthropic-version": "2023-06-01",
    }
    proxies = read_proxy() if use_proxy else None
    data = {
        "model": model or _CLAUDE_DEFAULT_MODEL,
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
        blocks = r.json().get("content") or [{}]
        result = (blocks[0] if blocks else {}).get("text", "").strip()
        if not result:
            # 空译文写进 NFO 会把标题/简介清空, 按失败处理更稳妥
            result = {'error_code': -3, 'error_msg': 'Claude 返回空译文'}
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
    }
    # api_key 可留空: 本地 Ollama / LM Studio 不需要鉴权, 留空时不发送该头
    # (旧实现是无条件 Bearer + 空串, 部分端点会因此拒绝请求)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
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
        # 只解析一次: 旧代码对同一个响应连调三次 r.json()
        payload = r.json()
        err = payload.get('error') if isinstance(payload, dict) else None
        if err:
            result = {
                "error_code": r.status_code,
                "error_msg": err.get("message", "") if isinstance(err, dict) else str(err),
            }
        else:
            choices = (payload.get("choices") or [{}]) if isinstance(payload, dict) else [{}]
            first = choices[0] if choices else {}
            result = (first.get("message") or {}).get("content", "").strip()
            if not result:
                # 空译文写进 NFO 会把标题/简介清空, 按失败处理更稳妥
                result = {'error_code': -3,
                          'error_msg': '模型返回空译文(检查模型名与端点是否匹配)'}
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
