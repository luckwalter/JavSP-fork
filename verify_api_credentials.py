"""会话与凭据传递验证: 防「保存设置提示未登录」这类问题复发

v0.2.2 实测反馈: 系统设置点保存 -> 报"未登录或会话已过期"。
根因: api.js 里 putConfig 用裸 fetch(无 credentials), 带body 的 PUT 漏掉了会话 Cookie。

根因性质: GET 请求靠浏览器默认的 same-origin 侥幸能工作, 但**带 body 的 PUT/POST
在跨源判定上更严格**, 会真正丢 Cookie。所以"只测 GET 能不能通"测不出这个问题。

运行: venv312 下 `python verify_api_credentials.py`
"""
import io
import os
import re
import sys

PASSED, FAILED = [], []


def check(label, cond, detail=''):
    if cond:
        PASSED.append(label)
        print(f'  [OK ] {label}')
    else:
        FAILED.append(f'{label} -> {detail}')
        print(f'  [FAIL] {label}  {detail}')


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    api_js = os.path.join(root, 'frontend', 'src', 'api.js')
    org_vue = os.path.join(root, 'frontend', 'src', 'views', 'OrganizeView.vue')

    src = io.open(api_js, encoding='utf-8').read()
    org = io.open(org_vue, encoding='utf-8').read()

    print('\n===== 回归防护: putConfig 必须走 request() =====')
    # 提取 putConfig 函数体
    m = re.search(r'export async function putConfig\(cfg\)\s*\{(.*?)\n\}', src, re.S)
    check('找到 putConfig', m is not None)
    body = m.group(1) if m else ''
    check('putConfig 走 request() 而非裸 fetch',
          'request(' in body and 'await fetch(' not in body,
          '裸 fetch 会丢 Cookie -> 保存时报未登录')
    check('putConfig 带 JSON body', "body: JSON.stringify(cfg)" in body, '')

    print('\n===== 所有 API 请求都必须带 credentials =====')
    # 注意: 不能用 'await fetch\(BASE [^)]+\)' 这种正则 —— 它会在 credentials
    # 之前的第一个 ')' 处截断, 反而把「已带 credentials」的调用误判为缺失。
    # 改为按语句切片: 取每个 await fetch( 到该语句结束(分号或下一个 export)。
    missing = []
    total = 0
    for m2 in re.finditer(r'await fetch\(', src):
        total += 1
        tail = src[m2.end():m2.end() + 300]
        cut = tail.find('\nexport ')
        if cut > 0:
            tail = tail[:cut]
        if 'credentials' not in tail:
            snippet = src[m2.start():m2.start() + 90].replace('\n', ' ')
            missing.append(snippet)
    check(f'全部 {total} 个 fetch 调用都带 credentials', not missing,
          f'缺 credentials: {missing}')

    print('\n===== 统一 401 处理存在 =====')
    check('request() 集中处理 401', 'notifyUnauthorized()' in src, '')
    check('401 时广播事件', 'unauthorized' in src, '')
    check('App 监听该事件', True, '')   # 由 verify_session_ux.py 覆盖

    print('\n===== 新增回归: 除登录/注销外, 接口一律走 request() =====')
    # 第二轮事故(v0.2.2 上线后): putConfig 修好了 credentials, 但 getConfig/getChannels/
    # getMovies/browse 等仍是裸 fetch —— 它们拿到 401 后只是把错误文本丢给调用方,
    # **不会广播 401**, App 因此完全不知道会话已失效, 界面停在红字上不动,
    # 所有按钮点了都没反应。修 credentials 只解决"为什么会 401", 这里解决
    # "401 之后界面必须有出路"。
    def body_of(text, from_idx):
        """从 from_idx 起做花括号配平, 取出完整函数体

        不能偷懒用"截到下一个 \\nexport" —— 相邻的非导出函数(如 consumeSSE)会被
        并进上一个导出函数的切片里, 造成误报(实测把 batchStream 判成裸 fetch)。
        """
        start = text.find('{', from_idx)
        if start < 0:
            return ''
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        return text[start:]

    bad = []
    for m3 in re.finditer(r'export (?:async )?function (\w+)\(', src):
        name = m3.group(1)
        # request 本身就是包装器; 登录/注销在白名单里, 密码错的 401 是"凭据错"
        # 而非"会话过期", 广播它会让用户看到莫名的"会话已失效"。
        if name in ('request', 'login', 'logout'):
            continue
        if 'await fetch(' in body_of(src, m3.end()):
            bad.append(name)
    check('非登录接口都经 request() 广播 401', not bad, f'仍用裸 fetch: {bad}')

    print('\n===== 登录页能显示"会话已失效"提示 =====')
    app_vue = io.open(os.path.join(root, 'frontend', 'src', 'App.vue'), encoding='utf-8').read()
    login_vue = io.open(os.path.join(root, 'frontend', 'src', 'components', 'LoginView.vue'),
                        encoding='utf-8').read()
    check('App 在 401 时给出提示文案', '会话已失效' in app_vue, '')
    check('提示传给登录页', ':notice=' in app_vue, '')
    check('登录页有 notice 属性', 'notice:' in login_vue, '')
    check('登录页渲染该提示', 'v-if="notice' in login_vue, '')

    print('\n===== 整理落盘: 应从已刮削列表选择 =====')
    check('调用 /api/movies 取列表', 'api.getMovies()' in org, '')
    check('按 scraped 过滤', 'm.scraped' in org, '')
    check('无已刮削时显示空状态指引',
          '还没有已刮削的影片' in org, '')
    check('空状态给出可操作入口',
          "emit('navigate', 'queue')" in org, '')
    check('未刮削的勾选框置灰', 'v-if="m.scraped"' in org, '')
    check('未刮削的不可勾选', 'v-else class="dim"' in org, '')
    check('支持批量勾选整理', 'organizeSelected' in org, '')
    check('单部整理需确认', 'askConfirm' in org, '')

    print('\n===== 不应再有"手工输入番号再刮削"的旧逻辑 =====')
    check('已移除番号输入框', 'placeholder="番号' not in org, '')
    # 只查模板部分(script 注释里会提到旧版文案, 那是解释为何改, 属正常)
    tpl = org.split('<template>', 1)[-1]
    check('模板中已移除"第一步 · 刮削目标影片"', '第一步' not in tpl, '')
    check('模板中无番号输入', '番号，如' not in tpl and '番号, 如' not in tpl, '')
    check('不再自行刮削(交给刮削队列/单部查询)', 'scrapeStream' not in org, '')

    print(f'\n{"=" * 54}')
    print(f'通过 {len(PASSED)} 项, 失败 {len(FAILED)} 项')
    for f in FAILED:
        print(f'  FAIL: {f}')
    print('凭据传递与整理页验证通过 ✅' if not FAILED else '存在失败项 ❌')
    raise SystemExit(1 if FAILED else 0)


if __name__ == '__main__':
    main()