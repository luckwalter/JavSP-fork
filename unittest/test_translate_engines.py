"""百度 / Bing / Claude / OpenAI 兼容 四个翻译通道的健壮性测试(全部 mock, 不联网)

背景: 逐通道调研发现, 除 Google(已在 test_google_translate.py 覆盖)外的四个
通道都存在"失败侧"短板, 其中百度/Bing 最严重 —— 它们的分支**完全没有异常兜底**,
网络异常会一路冒泡到 translate_movie_info: 标题翻译抛异常后简介根本不会执行,
整部影片的翻译全废, 而日志只剩一坨 requests 的异常 repr。

另外记录几个已修的具体缺陷(每个测试对应一条):
  · 百度错误码是英文且无排查方向(54003 到底是限流还是欠费?)
  · 百度 QPS 节流在多线程下形同虚设(时间戳在请求后才更新)
  · Bing 返回空列表时 result[0] 抛 IndexError
  · Bing 女优名保护把 <mstrans:dictionary> 标记写进 orig_break, 污染 NFO 原文断句
  · Bing 女优名未做 XML 转义(外部文本直接拼进属性值)
  · Claude 模型硬编码且默认型号已被官方硬退役
  · Claude 请求头拼写错误 context-type
  · OpenAI 兼容端 api_key 必填, 而本地 Ollama 根本不需要
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import javsp.web.translate as tr


class FakeResp:
    """最小 Response 替身"""

    def __init__(self, status_code=200, payload=None, text='', reason='OK'):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.reason = reason
        self.headers = {'content-type': 'application/json'}

    def json(self):
        if self._payload is None:
            raise ValueError('No JSON object could be decoded')
        return self._payload


class GoogleEngine:
    name = 'google'


class BaiduEngine:
    name = 'baidu'
    app_id = 'id'
    api_key = 'key'
    qps = 20.0        # 测试里调高, 避免节流拖慢用例


class BingEngine:
    name = 'bing'
    api_key = 'k'
    region = 'global'


class ClaudeEngine:
    name = 'claude'
    api_key = 'k'
    model = 'claude-haiku-4-5-20251001'
    use_proxy = False


class OpenAIEngine:
    name = 'openai'
    url = 'http://127.0.0.1:11434/v1/chat/completions'
    api_key = ''
    model = 'qwen3:14b'
    use_proxy = False


def _no_sleep(monkeypatch):
    monkeypatch.setattr(tr.time, 'sleep', lambda s: None)


def _patch_session(monkeypatch, handler):
    """LLM 引擎走 requests.Session(), 必须替换 Session 本身而非 requests.post"""
    class FakeSession:
        def __init__(self):
            self.trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return handler(*a, **k)

    monkeypatch.setattr(tr.requests, 'Session', FakeSession)


# ---------------------------------------------------------------- 百度

def test_baidu_network_error_is_readable(monkeypatch):
    """网络异常: 可读错误且不冒泡(旧实现会让整部影片翻译全废)"""
    _no_sleep(monkeypatch)
    import requests
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError('boom')

    monkeypatch.setattr(tr.requests, 'post', boom)
    r = tr.translate('Hello', BaiduEngine(), to='zh_CN')
    assert '无法连接百度翻译' in r.get('error', '')
    assert 'trans' not in r


def test_baidu_error_code_localized(monkeypatch):
    """错误码汉化: 54003 要能看出是限流而不是欠费"""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    monkeypatch.setattr(
        tr.requests, 'post',
        lambda *a, **k: FakeResp(200, payload={'error_code': '54003',
                                               'error_msg': 'Invalid Access Limit'}))
    r = tr.translate('Hello', BaiduEngine(), to='zh_CN')
    err = r.get('error', '')
    assert '访问频率受限' in err
    assert 'Invalid Access Limit' not in err, '不应再显示无法判断含义的英文原文'


def test_baidu_missing_trans_result_guarded(monkeypatch):
    """200 但缺 trans_result: 报结构异常而非 KeyError"""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    monkeypatch.setattr(tr.requests, 'post',
                        lambda *a, **k: FakeResp(200, payload={'foo': 'bar'}))
    r = tr.translate('Hello', BaiduEngine(), to='zh_CN')
    assert '响应结构异常' in r.get('error', '')


def test_baidu_success_joins_paragraphs(monkeypatch):
    """成功路径: 按段落(换行)拼接"""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    monkeypatch.setattr(
        tr.requests, 'post',
        lambda *a, **k: FakeResp(200, payload={'trans_result': [{'dst': '第一段'},
                                                                {'dst': '第二段'}]}))
    r = tr.translate('a\nb', BaiduEngine(), to='zh_CN')
    assert r['trans'] == '第一段\n第二段'


def test_baidu_qps_throttle_is_concurrent_safe():
    """QPS 节流: 连续预约的时间槽必须严格递增(并发下不会一起放行)

    旧实现在请求**之后**才更新时间戳, 多线程同时读到同一个旧值 -> 真实 QPS
    远超配置值, 反而更容易触发百度的 54003。
    """
    tr._throttle_next_slot.pop('baidu', None)
    slots = []
    import time

    def _probe():
        # 复制 _throttle_baidu 的核心: 进锁前记录, 由被测函数推进
        tr._throttle_baidu(10.0)      # 0.1s 间隔
        slots.append(tr._throttle_next_slot['baidu'])

    for _ in range(3):
        _probe()
    assert slots[0] < slots[1] < slots[2], f'时间槽应严格递增, 实际 {slots}'
    assert time is not None


# ---------------------------------------------------------------- Bing

def test_bing_network_error_is_readable(monkeypatch):
    _no_sleep(monkeypatch)
    import requests
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})

    def boom(*a, **k):
        raise requests.exceptions.ConnectTimeout('timeout')

    monkeypatch.setattr(tr.requests, 'post', boom)
    r = tr.translate('Hello', BingEngine(), to='zh_CN')
    assert '无法连接 Bing' in r.get('error', '')


def test_bing_empty_response_no_indexerror(monkeypatch):
    """空列表响应: 报结构异常(旧实现 result[0] 直接 IndexError)"""
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    monkeypatch.setattr(tr.requests, 'post',
                        lambda *a, **k: FakeResp(200, payload=[]))
    r = tr.translate('Hello', BingEngine(), to='zh_CN')
    assert '响应结构异常' in r.get('error', '')


def test_bing_actress_name_not_polluting_orig_break(monkeypatch):
    """女优名保护不得污染 orig_break

    sentLen 是按**替换后**文本给出的长度, 旧代码直接把带 <mstrans:dictionary>
    标记的片段写进 orig_break, 导致 NFO 里的原文断句被 XML 标记污染。
    """
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    sent_box = {}

    def fake_post(*a, **kw):
        sent = kw['json'][0]['text']
        sent_box['sent'] = sent
        return FakeResp(200, payload=[{'translations': [{
            'text': '译文。',
            'sentLen': {'srcSentLen': [len(sent)], 'transSentLen': [len('译文。')]}}]}])

    monkeypatch.setattr(tr.requests, 'post', fake_post)
    r = tr.translate('三上悠亜の新作です。', BingEngine(), actress=['三上悠亜'], to='zh_CN')
    assert '<mstrans' in sent_box['sent'], '请求里应带动态词典标记(否则女优名会被译掉)'
    joined = ''.join(r['orig_break'])
    assert '<mstrans' not in joined, 'orig_break 不得残留 XML 标记'
    assert '三上悠亜' in joined, '还原后应是原始女优名'


def test_bing_actress_name_xml_escaped(monkeypatch):
    """女优名含 XML 特殊字符时必须转义(外部文本直接拼属性值会破坏结构)"""
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    sent_box = {}

    def fake_post(*a, **kw):
        sent = kw['json'][0]['text']
        sent_box['sent'] = sent
        return FakeResp(200, payload=[{'translations': [{
            'text': 'x',
            'sentLen': {'srcSentLen': [len(sent)], 'transSentLen': [1]}}]}])

    monkeypatch.setattr(tr.requests, 'post', fake_post)
    r = tr.translate('A&B の作品', BingEngine(), actress=['A&B'], to='zh_CN')
    assert '&amp;' in sent_box['sent'], '属性值里的 & 必须转义'
    assert 'A&B' in ''.join(r['orig_break']), '还原后仍是原始女优名'


def test_bing_region_forwarded(monkeypatch):
    """资源区域可配(单服务资源填实际区域, 否则 401001 无从排查)"""
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    seen = {}

    def fake_post(*a, **kw):
        seen['headers'] = kw.get('headers', {})
        return FakeResp(200, payload=[{'translations': [{
            'text': 'x', 'sentLen': {'srcSentLen': [1], 'transSentLen': [1]}}]}])

    monkeypatch.setattr(tr.requests, 'post', fake_post)

    class E(BingEngine):
        region = 'eastasia'

    tr.translate('Hello', E(), to='zh_CN')
    assert seen['headers'].get('Ocp-Apim-Subscription-Region') == 'eastasia'


# ---------------------------------------------------------------- Claude

def test_claude_default_model_not_retired():
    """默认模型不得是已退役的 claude-3-haiku-20240307

    该型号自 2026-04-20 起被 Anthropic 硬退役, 对该 ID 的请求直接报错 —— 即用户
    什么都没配错也必然失败。这条断言防止有人把旧默认值改回去。
    """
    assert tr._CLAUDE_DEFAULT_MODEL != 'claude-3-haiku-20240307'
    assert tr._CLAUDE_DEFAULT_MODEL.startswith('claude-')


def test_claude_model_forwarded(monkeypatch):
    """模型名可配(旧实现硬编码, 退役型号改不了只能等升级)"""
    seen = {}

    def handler(*a, **kw):
        seen['json'] = kw.get('json', {})
        return FakeResp(200, payload={'content': [{'text': '译文'}]})

    _patch_session(monkeypatch, handler)

    class E(ClaudeEngine):
        model = 'claude-sonnet-4-6'

    r = tr.translate('こんにちは', E(), to='zh_CN')
    assert seen['json']['model'] == 'claude-sonnet-4-6'
    assert r['trans'] == '译文'


def test_claude_no_bogus_content_type_header(monkeypatch):
    """旧代码的 context-type 拼写错误头必须已删除"""
    seen = {}

    def handler(*a, **kw):
        seen['headers'] = kw.get('headers', {})
        return FakeResp(200, payload={'content': [{'text': '译文'}]})

    _patch_session(monkeypatch, handler)
    tr.translate('こんにちは', ClaudeEngine(), to='zh_CN')
    assert 'context-type' not in seen['headers']


def test_claude_empty_translation_is_error(monkeypatch):
    """空译文按失败处理(否则会把 NFO 标题/简介清空)"""

    def handler(*a, **kw):
        return FakeResp(200, payload={'content': [{'text': '   '}]})

    _patch_session(monkeypatch, handler)
    r = tr.translate('こんにちは', ClaudeEngine(), to='zh_CN')
    assert 'error' in r


# ---------------------------------------------------------------- OpenAI 兼容

def test_openai_no_auth_header_when_key_empty(monkeypatch):
    """本地 Ollama 不需要密钥: 留空时不发 Authorization 头"""
    seen = {}

    def handler(*a, **kw):
        seen['headers'] = kw.get('headers', {})
        return FakeResp(200, payload={'choices': [{'message': {'content': '译文'}}]})

    _patch_session(monkeypatch, handler)
    r = tr.translate('こんにちは', OpenAIEngine(), to='zh_CN')
    assert 'Authorization' not in seen['headers']
    assert r['trans'] == '译文'


def test_openai_auth_header_when_key_present(monkeypatch):
    """填了密钥时正常发送(不能因为支持留空就把鉴权弄丢)"""
    seen = {}

    def handler(*a, **kw):
        seen['headers'] = kw.get('headers', {})
        return FakeResp(200, payload={'choices': [{'message': {'content': '译文'}}]})

    _patch_session(monkeypatch, handler)

    class E(OpenAIEngine):
        api_key = 'sk-test'

    tr.translate('こんにちは', E(), to='zh_CN')
    assert seen['headers'].get('Authorization') == 'Bearer sk-test'


# ---------------------------------------------------------------- 共性: 分段

def test_long_text_auto_split(monkeypatch):
    """超长文本自动分段(各引擎单次请求有硬上限, 超限整条失败)"""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    calls = []

    def fake_get(url, *a, **k):
        calls.append(url)
        return FakeResp(200, payload={'sentences': [{'orig': 'x', 'trans': '译'}]})

    monkeypatch.setattr(tr.requests, 'get', fake_get)
    long_text = ('あ' * 400 + '。') * 8          # 约 9600 字节 > 6000 上限
    r = tr.translate(long_text, GoogleEngine(), to='zh_CN')
    assert len(calls) >= 2, f'应分成多段请求, 实际只发 {len(calls)} 次'
    assert r['trans'] == '译' * len(calls)


def test_short_text_single_request(monkeypatch):
    """未超限时不分段(行为与旧版一致, 不引入额外请求)"""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    calls = []

    def fake_get(url, *a, **k):
        calls.append(url)
        return FakeResp(200, payload={'sentences': [{'orig': 'Hello', 'trans': '你好'}]})

    monkeypatch.setattr(tr.requests, 'get', fake_get)
    r = tr.translate('Hello', GoogleEngine(), to='zh_CN')
    assert len(calls) == 1
    assert r['trans'] == '你好'


# ---------------------------------------------------------------- 共性: 缓存

def _enable_cache(monkeypatch, tmp_path):
    """把缓存打开并指向临时目录(默认 conftest 里是关的)"""
    monkeypatch.setattr(tr.transcache, 'enabled', lambda: True)
    monkeypatch.setattr(tr.transcache, 'cache_dir', lambda: str(tmp_path))


def test_cache_hit_skips_engine_call(monkeypatch, tmp_path):
    """同一文本第二次命中缓存, 不再消耗 API 配额"""
    _no_sleep(monkeypatch)
    _enable_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return FakeResp(200, payload={'trans_result': [{'dst': '你好'}]})

    monkeypatch.setattr(tr.requests, 'post', fake_post)
    tr.translate('Hello', BaiduEngine(), to='zh_CN')
    tr.translate('Hello', BaiduEngine(), to='zh_CN')
    assert len(calls) == 1, f'第二次应命中缓存, 实际请求 {len(calls)} 次'


def test_cache_miss_after_model_change(monkeypatch, tmp_path):
    """换模型必须重新翻译, 不能复用旧译文"""
    _enable_cache(monkeypatch, tmp_path)
    calls = []

    def handler(*a, **kw):
        calls.append(kw.get('json', {}).get('model'))
        return FakeResp(200, payload={'content': [{'text': '译文'}]})

    _patch_session(monkeypatch, handler)
    tr.translate('こんにちは', ClaudeEngine(), to='zh_CN')

    class E(ClaudeEngine):
        model = 'claude-sonnet-4-6'

    tr.translate('こんにちは', E(), to='zh_CN')
    assert len(calls) == 2, f'换模型后应重新调用引擎, 实际 {calls}'


def test_cache_miss_after_language_change(monkeypatch, tmp_path):
    """换目标语种必须重新翻译"""
    _no_sleep(monkeypatch)
    _enable_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return FakeResp(200, payload={'trans_result': [{'dst': '你好'}]})

    monkeypatch.setattr(tr.requests, 'post', fake_post)
    tr.translate('Hello', BaiduEngine(), to='zh_CN')
    tr.translate('Hello', BaiduEngine(), to='en')
    assert len(calls) == 2, f'换语种后应重新调用引擎, 实际 {calls} 次'
