"""翻译改造第二轮加固(B-014 ~ B-020)的回归测试

第一轮改造(百度/Bing/Claude/OpenAI/Google 五通道的失败侧)做完后的安全与性能
审查又查出 7 项缺陷, 本文件逐条固化。与 test_translate_engines.py 的分工:
那个文件覆盖"引擎能不能用", 本文件覆盖"用起来安不安全、会不会拖垮自己"。

  B-014  测试端点可被当作回显型 SSRF / 配额消耗器(URL 任意、无频控、报错回显凭据)
  B-015  Bing 女优名动态词典标记会嵌套(名字互为子串时产出畸形 XML)
  B-016  Google 成功路径固定 sleep(4) 且按段计; 429 退避没有总预算
  B-017  Google 单次 6000 字节 -> URL 约 18000 字符, 超中间设备上限
  B-018  引擎异常信息里带出完整 URL(百度的 appid/sign 就在查询串上)
  B-019  翻译缓存无条数上限, 清理后不 VACUUM(点了"清空"但占用字节纹丝不动)
  B-020  长文本分段后按空串拼接, 百度的段落语义(换行)丢失

全部用例均为离线 mock, 不联网、不消耗任何真实配额。
"""
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import javsp.web.translate as tr
import javsp.transcache as tc


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
    qps = 20.0


class BingEngine:
    name = 'bing'
    api_key = 'k'
    region = 'global'


def _no_sleep(monkeypatch):
    monkeypatch.setattr(tr.time, 'sleep', lambda s: None)


def _clear_google_slot():
    """清掉 Google 的引擎级节流槽位, 让用例只观察目标行为"""
    tr._throttle_next_slot.pop('google', None)


# ============================================================ B-014 测试端点

def test_test_url_rejects_dangerous_targets():
    """SSRF 面: 非 http(s) 协议与云元数据地址一律拒绝"""
    from javsp.server import _validate_test_url

    bad = (
        'file:///etc/passwd',                       # 本地文件读取
        'ftp://example.com/x',                      # 非白名单协议
        'http://169.254.169.254/latest/meta-data/',  # AWS/Azure/腾讯云元数据
        'http://169.254.170.2/v2/credentials',       # ECS task 凭据
        'http://100.100.100.200/latest/meta-data/',  # 阿里云元数据
        'http://metadata.google.internal/',          # GCP 元数据域名
        'http://[fd00:ec2::254]/',                   # AWS IPv6 元数据
        'http://[::ffff:169.254.169.254]/',          # IPv4 映射写法绕过
        'http://0.0.0.0:8080/',                      # 未指定地址
        'http://',                                   # 缺主机名
    )
    for url in bad:
        assert _validate_test_url(url), f'{url} 应被拒绝'


def test_test_url_allows_local_and_lan_llm():
    """本地 Ollama / 局域网自建 LLM 必须放行 —— 那是该引擎的核心用法

    这是本项修复的**取舍点**: 一刀切禁私有网段会直接废掉主人自己
    (192.168.2.100 上的 Ollama)的用法, 所以只挡元数据地址。
    """
    from javsp.server import _validate_test_url

    good = (
        'http://127.0.0.1:11434/v1/chat/completions',
        'http://localhost:11434/v1/chat/completions',
        'http://192.168.2.100:11434/v1/chat/completions',
        'http://ollama.local:11434/v1/chat/completions',   # 域名形态不做 DNS 解析
        'https://api.openai.com/v1/chat/completions',
        'https://open.bigmodel.cn/api/paas/v4/chat/completions',
    )
    for url in good:
        assert _validate_test_url(url) == '', f'{url} 应被放行, 实际: {_validate_test_url(url)}'


def test_translate_test_rate_limited():
    """配额保护: 同一客户端连点必须被拦, 不同客户端互不影响"""
    from javsp.server import _test_last_call, _test_too_soon

    _test_last_call.clear()
    assert _test_too_soon('198.51.100.1') is False, '首次调用应放行'
    assert _test_too_soon('198.51.100.1') is True, '紧接着的第二次应被拦'
    assert _test_too_soon('198.51.100.2') is False, '不同客户端互不影响'
    # 间隔足够久之后应重新放行(验证的是"间隔"而非"一次性闭锁")
    _test_last_call['198.51.100.1'] -= 3600
    assert _test_too_soon('198.51.100.1') is False
    _test_last_call.clear()


def test_translate_test_endpoint_rate_limit_wired(monkeypatch):
    """限流必须真的挂在端点上(而不只是有个函数): 第二次请求返回 429"""
    from fastapi.testclient import TestClient
    import javsp.server as srv

    srv._test_last_call.clear()
    # mock 掉真实翻译, 否则会打外部接口
    monkeypatch.setattr(srv, 'translate', lambda *a, **k: {'trans': '译文'})
    client = TestClient(srv.app)
    payload = {'engine': {'name': 'google'}}

    r1 = client.post('/api/translate/test', json=payload)
    assert r1.status_code == 200, r1.text
    r2 = client.post('/api/translate/test', json=payload)
    assert r2.status_code == 429, f'连续第二次测试应被限流, 实际 {r2.status_code}'
    assert '配额' in r2.json().get('detail', '')
    srv._test_last_call.clear()


def test_translate_test_error_hides_credentials():
    """配置校验失败时不得回显 api_key

    pydantic 的默认报错会**原样带上输入值**, 而 discriminated union 匹配失败时
    输入就是整个 engine dict(含 api_key)。这段文本会直接显示在设置页上, 也会
    被用户贴进 issue。
    """
    from fastapi.testclient import TestClient
    import javsp.server as srv

    srv._test_last_call.clear()
    client = TestClient(srv.app)
    r = client.post('/api/translate/test', json={
        'engine': {'name': '不存在的引擎', 'api_key': 'sk-SECRET-123456',
                   'url': 'http://169.254.169.254/'}})
    assert r.status_code == 400
    body = r.text
    assert 'sk-SECRET-123456' not in body, f'响应回显了 api_key: {body}'
    assert '169.254.169.254' not in body, f'响应回显了目标地址: {body}'
    srv._test_last_call.clear()


# ============================================================ B-015 Bing 词典标记

# 嵌套标记: 一个 <mstrans:dictionary> 开标签之后, 还没遇到闭合就又出现开标签
_NESTED_MARK_RE = re.compile(
    r'<mstrans:dictionary[^>]*>(?:(?!</mstrans:dictionary>).)*?<mstrans:dictionary', re.S)


def _bing_capture(monkeypatch):
    """替换 Bing 的 post, 把实际发出的 text 抓出来"""
    box = {}

    def fake_post(*a, **kw):
        sent = kw['json'][0]['text']
        box['sent'] = sent
        return FakeResp(200, payload=[{'translations': [{
            'text': '译文',
            'sentLen': {'srcSentLen': [len(sent)], 'transSentLen': [len('译文')]}}]}])

    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    monkeypatch.setattr(tr.requests, 'post', fake_post)
    return box


def test_bing_actress_marks_never_nest(monkeypatch):
    """女优名互为子串时不得产出嵌套标记(无论名单顺序如何)

    旧实现是 for 循环逐个 str.replace: ['佐藤美咲','佐藤'] 顺序下, 先给长名打上
    标记, 再处理短名时会在**标记内部**再替换一次 -> 畸形 XML, Bing 要么忽略词典
    (女优名被译掉)要么直接报错。
    """
    box = _bing_capture(monkeypatch)
    for order in (['佐藤美咲', '佐藤'], ['佐藤', '佐藤美咲']):
        box.clear()
        tr.translate('佐藤美咲の新作です。', BingEngine(), actress=order, to='zh_CN')
        sent = box['sent']
        assert not _NESTED_MARK_RE.search(sent), f'出现嵌套标记: {sent}'
        assert sent.count('<mstrans:dictionary') == 1, f'应只标记一次: {sent}'


def test_bing_actress_longest_name_wins(monkeypatch):
    """长名优先: 名单里短名在前时也不能把长名切断"""
    box = _bing_capture(monkeypatch)
    tr.translate('佐藤美咲の新作です。', BingEngine(), actress=['佐藤', '佐藤美咲'],
                 to='zh_CN')
    sent = box['sent']
    assert '<mstrans:dictionary translation="佐藤美咲">佐藤美咲</mstrans:dictionary>' in sent, \
        f'长名应完整匹配: {sent}'


def test_bing_actress_marks_still_work(monkeypatch):
    """加固不得破坏原有功能: 标记仍在, 且还原后是原始女优名"""
    box = _bing_capture(monkeypatch)
    r = tr.translate('三上悠亜の新作です。', BingEngine(), actress=['三上悠亜'],
                     to='zh_CN')
    assert '<mstrans:dictionary' in box['sent'], '标记本身不能丢'
    joined = ''.join(r['orig_break'])
    assert '<mstrans' not in joined, 'orig_break 不得残留标记'
    assert '三上悠亜' in joined


# ============================================================ B-016 Google 等待策略

def test_google_success_has_no_fixed_sleep(monkeypatch):
    """成功路径不再固定 sleep(4)

    旧实现每成功一段就睡 4 秒: 长简介分 5 段 = 20 秒纯等待, 且各线程各睡各的,
    对服务端而言并没有真正限速。改为引擎级节流后, 首个请求无需等待。
    """
    sleeps = []
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    monkeypatch.setattr(tr.time, 'sleep', lambda s: sleeps.append(s))
    monkeypatch.setattr(tr.requests, 'get',
                        lambda *a, **k: FakeResp(200, payload={
                            'sentences': [{'orig': 'x', 'trans': '译'}]}))
    _clear_google_slot()
    tr.google_trans('x', to='zh_CN')
    assert sleeps == [], f'成功路径不应再有固定等待, 实际 {sleeps}'


def test_google_throttle_is_engine_level(monkeypatch):
    """节流是引擎级的: 后续调用要等前一个槽位(所有线程共享同一槽)"""
    sleeps = []
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    monkeypatch.setattr(tr.time, 'sleep', lambda s: sleeps.append(s))
    monkeypatch.setattr(tr.requests, 'get',
                        lambda *a, **k: FakeResp(200, payload={
                            'sentences': [{'orig': 'x', 'trans': '译'}]}))
    _clear_google_slot()
    tr.google_trans('x', to='zh_CN')
    first = list(sleeps)
    sleeps.clear()
    tr.google_trans('x', to='zh_CN')
    second = list(sleeps)

    assert first == [], '首次调用不该等'
    assert len(second) == 1, f'第二次应等一个槽位, 实际 {second}'
    assert 0 < second[0] <= tr._GOOGLE_MIN_INTERVAL + 1e-6


def test_google_backoff_has_total_budget(monkeypatch):
    """429 退避必须有累计预算: 不能让一次调用把线程占满几分钟"""
    sleeps = []
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    monkeypatch.setattr(tr.time, 'sleep', lambda s: sleeps.append(s))
    monkeypatch.setattr(tr.requests, 'get',
                        lambda *a, **k: FakeResp(429, reason='Too Many Requests'))
    _clear_google_slot()
    out = tr.google_trans('x', to='zh_CN')

    assert out.get('error_code') == 429
    assert sleeps, '持续 429 时应有退避等待'
    assert sum(sleeps) <= tr._GOOGLE_BACKOFF_BUDGET, \
        f'累计退避 {sum(sleeps)} 秒超过预算 {tr._GOOGLE_BACKOFF_BUDGET}'
    assert max(sleeps) <= tr._GOOGLE_BACKOFF_CAP


# ============================================================ B-017 Google URL 长度

def test_google_chunk_url_within_safe_length():
    """按引擎上限切出的单段, 编码后的 URL 必须留在安全长度内

    实测 6000 字节的原文经 quote(safe='') 后 URL 长达 18100 字符 —— 远超多数
    中间设备(nginx/代理/浏览器)8KB 的 URL 上限, 会被截断或直接 414。
    """
    from urllib.parse import quote

    max_bytes = tr._ENGINE_MAX_BYTES['google']
    # 用最坏情况填充: 中文按 3 字节计, 编码后每字节变 3 个字符(%XX)
    text = 'あ' * (max_bytes // 3)
    url = ("https://translate.google.com.hk/translate_a/single?client=gtx&dt=t&dj=1"
           f"&ie=UTF-8&sl=auto&tl=zh-CN&q={quote(text, safe='')}")
    assert len(url) < 8192, f'单段 URL 仍达 {len(url)} 字符'
    # 顺带确认上限确实被收紧过(不是 6000 那个旧值)
    assert max_bytes <= 2000, f'Google 上限应已收紧, 实际 {max_bytes}'


# ============================================================ B-018 错误信息脱敏

def test_redact_url_strips_query():
    """URL 查询串整体抹掉(百度的 appid/sign 就在那里)"""
    raw = ('HTTPError: 502 Server Error for url: '
           'https://fanyi-api.baidu.com/api/trans/vip/translate'
           '?appid=20240101&salt=9&sign=deadbeefcafe&q=hello')
    out = tr.redact_url(raw)
    assert 'deadbeefcafe' not in out and '20240101' not in out
    assert 'https://fanyi-api.baidu.com/api/trans/vip/translate' in out, \
        '主机名与路径应保留, 否则没法排查是哪个通道出问题'
    assert '<query omitted>' in out


def test_engine_error_hides_url_credentials(monkeypatch):
    """端到端: 引擎抛出的异常进 err_msg 时不得带出查询串"""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})

    def boom(*a, **k):
        # 故意用非 RequestException: 这类异常不会被引擎内部的网络兜底截住,
        # 会一路冒泡到 _translate_chunk 的 except(旧实现直接 repr 整条异常)
        raise RuntimeError('boom for url: https://fanyi-api.baidu.com/api/trans/vip/'
                           'translate?appid=20240101&sign=deadbeefcafe')

    monkeypatch.setattr(tr.requests, 'post', boom)
    r = tr.translate('Hello', BaiduEngine(), to='zh_CN')
    err = r.get('error', '')
    assert 'deadbeefcafe' not in err, f'错误里泄漏了签名: {err}'
    assert 'appid=20240101' not in err, f'错误里泄漏了 appid: {err}'
    assert err.startswith('baidu:'), f'错误信息仍应保持可读, 实际: {err}'


def test_error_repr_is_truncated(monkeypatch):
    """异常 repr 必须截断: 一页 HTML 不该被整坨灌进日志与界面"""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})

    def boom(*a, **k):
        raise RuntimeError('X' * 5000)

    monkeypatch.setattr(tr.requests, 'post', boom)
    r = tr.translate('Hello', BaiduEngine(), to='zh_CN')
    assert len(r.get('error', '')) < 400, '错误信息应被截断'


# ============================================================ B-019 缓存容量

def _enable_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(tc, 'enabled', lambda: True)
    monkeypatch.setattr(tc, 'cache_dir', lambda: str(tmp_path))


def test_cache_clear_shrinks_file(monkeypatch, tmp_path):
    """清空后文件必须真正变小

    DELETE 只把页标记为空闲(可复用), sqlite 文件本身不缩小 —— 用户在设置页点了
    "清空"却看到占用字节纹丝不动, 只会以为清理没生效。
    """
    _enable_cache(monkeypatch, tmp_path)
    for i in range(50):
        tc.put(f'k{i:02d}', {'trans': '译' * 200})
    before = tc.stats()
    assert before['count'] == 50, f"写入 50 条, 实际 {before['count']}"

    tc.clear()
    after = tc.stats()
    assert after['count'] == 0
    assert after['size'] < before['size'], \
        f"清空后文件应收缩, 实际 {before['size']} -> {after['size']}"


def test_cache_prune_shrinks_file(monkeypatch, tmp_path):
    """按保留天数清理同样要 VACUUM"""
    _enable_cache(monkeypatch, tmp_path)
    # 前 40 条写成"两天前"
    seq = iter([1000.0 + i for i in range(200)])
    monkeypatch.setattr(tc.time, 'time', lambda: float(next(seq)))
    for i in range(40):
        tc.put(f'k{i:02d}', {'trans': '译' * 200})
    before = tc.stats()['size']

    # 时间推进两天后再写 40 条新的, 然后按 retention_days=1 清理
    monkeypatch.setattr(tc.time, 'time', lambda: 1000.0 + 2 * 86400)
    for i in range(40, 80):
        tc.put(f'k{i:02d}', {'trans': '译' * 200})

    n = tc.prune(retention_days=1)
    assert n == 40, f'只应清理两天前的 40 条, 实际 {n}'
    after = tc.stats()
    assert after['count'] == 40, '新写入的条目必须保留'
    assert after['size'] < before * 1.5, \
        f'清理后文件应回落到接近初始大小: {before} -> {after["size"]}'


def test_cache_evicts_when_over_limit(monkeypatch, tmp_path):
    """条数达上限时按时间淘汰最旧的(长期运行的实例不会无限增长)"""
    _enable_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(tc, '_MAX_ROWS', 20)
    monkeypatch.setattr(tc, '_CHECK_EVERY', 1)
    # 让 created 严格递增, 否则同一毫秒内的条目无法区分新旧
    seq = iter(range(1000, 1000 + 60))
    monkeypatch.setattr(tc.time, 'time', lambda: float(next(seq)))

    for i in range(30):
        tc.put(f'k{i:02d}', {'trans': f'译{i}'})

    info = tc.stats()
    assert info['count'] <= 20, f"超过上限后应被淘汰, 实际 {info['count']} 条"
    assert info['max'] == 20, '统计应带上限, 便于前端展示'
    assert tc.get('k00') is None, '最旧的条目应被淘汰'
    assert tc.get('k29') is not None, '最新的条目必须保留'


def test_cache_below_limit_keeps_everything(monkeypatch, tmp_path):
    """未达上限时不得误删(淘汰逻辑不能过于激进)"""
    _enable_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(tc, '_CHECK_EVERY', 1)
    for i in range(10):
        tc.put(f'k{i:02d}', {'trans': f'译{i}'})
    assert tc.stats()['count'] == 10
    assert tc.get('k00') is not None


# ============================================================ B-020 分段拼接

def test_baidu_split_joins_with_newline(monkeypatch):
    """百度分段后按换行拼接: 段落语义不能丢

    百度的 trans_result 是按换行分隔的**段落**, dst 里不带换行(由上层 join 重建)。
    分段边界常落在句号上(段末没有换行), 若继续用空串拼接, 原文里"段A\n段B"
    就会塌成"段A段B" —— NFO 的多行简介会并成一行。
    """
    _no_sleep(monkeypatch)
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return FakeResp(200, payload={'trans_result': [{'dst': '译文段'}]})

    monkeypatch.setattr(tr.requests, 'post', fake_post)
    long_text = ('あ' * 200 + '。') * 40       # 约 24000 字节 > 6000 上限
    r = tr.translate(long_text, BaiduEngine(), to='zh_CN')
    assert len(calls) >= 2, f'应分成多段, 实际 {len(calls)} 次'
    assert r['trans'] == '\n'.join(['译文段'] * len(calls)), \
        f"百度应按换行拼接, 实际: {r['trans'][:60]!r}"


def test_google_split_keeps_empty_separator(monkeypatch):
    """Google/Bing 的句子是连续文本, 保持空串拼接(不要误加换行)"""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    calls = []

    def fake_get(url, *a, **k):
        calls.append(url)
        return FakeResp(200, payload={'sentences': [{'orig': 'x', 'trans': '译'}]})

    monkeypatch.setattr(tr.requests, 'get', fake_get)
    long_text = ('あ' * 200 + '。') * 10       # 约 6000 字节 > 1800 上限
    r = tr.translate(long_text, GoogleEngine(), to='zh_CN')
    assert len(calls) >= 2, f'应分成多段, 实际 {len(calls)} 次'
    assert r['trans'] == '译' * len(calls), f"Google 应连续拼接, 实际: {r['trans']!r}"
    assert '\n' not in r['trans']
