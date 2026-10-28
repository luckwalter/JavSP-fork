"""Google 翻译引擎健壮性测试

背景: issue #1 下用户反馈"谷歌翻译好像会失败"。实测接口本身是活的(能连上时
正常返回译文, 且不需要 UA), 真正的短板全在**失败侧**:
  1. 网络层异常(国内直连 Google 最常见)被 repr 成一大坨, 用户既看不懂也不
     知道要去配代理, 只能反馈"翻译失败";
  2. HTTP 200 但响应不是 JSON(拦截页/劫持)时 r.json() 抛异常, 同样被当成
     "未知异常", 真实响应内容被完全盖掉;
  3. 429 退避用模块级全局变量 _google_trans_wait, 每次只增且从不重置 →
     等待时间跨调用永久累积(一次限流后所有后续翻译都被拖慢);
  4. result['error_code'] 直接下标取值, 响应结构意外时抛 KeyError 并被兜成
     "Exception: KeyError('error_code')", 把真实原因盖掉。

本文件用 mock 覆盖以上分支, **全部不联网**。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import javsp.web.translate as tr


class FakeResp:
    """最小 Response 替身: status_code / reason / text / headers / json()"""

    def __init__(self, status_code=200, text='', payload=None,
                 reason='OK', ctype='application/json'):
        self.status_code = status_code
        self.text = text
        self._payload = payload
        self.reason = reason
        self.headers = {'content-type': ctype}

    def json(self):
        if self._payload is None:
            raise ValueError('No JSON object could be decoded')
        return self._payload


class GoogleEngine:
    name = 'google'


def _no_sleep(monkeypatch):
    """屏蔽真实等待: 成功路径有 4 秒 QPS sleep, 429 路径最长可达数分钟"""
    monkeypatch.setattr(tr.time, 'sleep', lambda s: None)


def test_google_success(monkeypatch):
    """走通时正常拼接译文(句子分组需自行 join)"""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    payload = {'sentences': [{'orig': 'Hello world', 'trans': '你好世界'}], 'src': 'en'}
    monkeypatch.setattr(tr.requests, 'get', lambda *a, **k: FakeResp(200, payload=payload))
    r = tr.translate('Hello world', GoogleEngine(), to='zh_CN')
    assert r.get('trans') == '你好世界'
    assert r['trans_break'] == ['你好世界']
    assert r['orig_break'] == ['Hello world']


def test_google_network_failure_is_readable(monkeypatch):
    """网络层失败: 压缩成可读原因 + 给出排查方向, 不再抛整坨 repr"""
    _no_sleep(monkeypatch)
    import requests
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})

    def boom(*a, **k):
        raise requests.exceptions.ProxyError('Tunnel connection failed: 502 Bad Gateway')

    monkeypatch.setattr(tr.requests, 'get', boom)
    r = tr.translate('Hello world', GoogleEngine(), to='zh_CN')
    err = r.get('error', '')
    assert '无法连接 Google 翻译' in err
    assert 'network.proxy_server' in err, '应提示代理排查方向'
    assert 'MaxRetryError' not in err, '不应再暴露 requests 内部 repr'


def test_google_non_json_response(monkeypatch):
    """HTTP 200 但返回 HTML(拦截页/劫持): 报 -3 并带上响应片段"""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    monkeypatch.setattr(
        tr.requests, 'get',
        lambda *a, **k: FakeResp(200, text='<html>blocked</html>', ctype='text/html'))
    out = tr.google_trans('Hello world', to='zh_CN')
    assert out.get('error_code') == -3
    assert '非JSON' in out.get('error_msg', '')
    assert 'blocked' in out.get('error_msg', ''), '应带上真实响应片段便于诊断'


def test_google_rate_limited(monkeypatch):
    """持续 429: 重试耗尽后返回 429"""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    monkeypatch.setattr(
        tr.requests, 'get',
        lambda *a, **k: FakeResp(429, reason='Too Many Requests'))
    out = tr.google_trans('Hello world', to='zh_CN')
    assert out.get('error_code') == 429


def test_google_unexpected_payload_no_keyerror(monkeypatch):
    """响应结构意外时不抛 KeyError, 错误仍可读(旧实现会盖成 KeyError)"""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    monkeypatch.setattr(tr.requests, 'get', lambda *a, **k: FakeResp(200, payload={}))
    r = tr.translate('Hello world', GoogleEngine(), to='zh_CN')
    err = r.get('error', '')
    assert 'KeyError' not in err, '不应再出现 KeyError'
    assert err.startswith('google:')


def test_google_backoff_not_accumulated(monkeypatch):
    """退避等待不得跨调用累积

    旧实现命中一次 429 后 _google_trans_wait 只增不重置, 后续每一次翻译都从
    最长的等待起步, 表现为"越翻越慢直至卡死"。改为局部退避后, 两次调用的
    等待序列必须完全一致。

    注: 本用例只比较**退避**序列, 故每次调用前清掉 Google 的引擎级节流槽位
    (_throttle), 否则第二次调用会凭空多出一次节流等待。
    """
    sleeps = []
    monkeypatch.setattr(tr, 'read_proxy', lambda: {})
    monkeypatch.setattr(tr.time, 'sleep', lambda s: sleeps.append(s))
    monkeypatch.setattr(
        tr.requests, 'get',
        lambda *a, **k: FakeResp(429, reason='Too Many Requests'))

    tr._throttle_next_slot.pop('google', None)
    tr.google_trans('x', to='zh_CN')
    first = list(sleeps)
    sleeps.clear()
    tr._throttle_next_slot.pop('google', None)
    tr.google_trans('x', to='zh_CN')
    second = list(sleeps)

    assert first, '持续 429 时应有退避等待'
    assert first == second, f'两次调用退避序列应一致, 实际 {first} vs {second}'
    assert max(first) <= tr._GOOGLE_BACKOFF_CAP, '单次退避不得超过上限'
    assert sum(first) <= tr._GOOGLE_BACKOFF_BUDGET, '累计退避不得超过总预算'
