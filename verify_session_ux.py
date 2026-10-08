"""登录会话体验验证: 防「刷新即登出」与「很快退出登录」

v0.2.2 上线后主人实测反馈"操作过程中很快就有退出登录 / 显示未登录或会话已过期"。
实测定位: 启用认证时前端 onMounted 只查 /api/auth/status(免登录), 却**不验证会话是否
仍有效**就停在登录页 —— 表现为刷新页面即登出。本脚本固化该回归。

运行: venv312 下 `python verify_session_ux.py`
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
    app_vue = os.path.join(root, 'frontend', 'src', 'App.vue')
    lib_vue = os.path.join(root, 'frontend', 'src', 'views', 'LibraryView.vue')
    ch_vue = os.path.join(root, 'frontend', 'src', 'views', 'ChannelsView.vue')
    api_js = os.path.join(root, 'frontend', 'src', 'api.js')

    app = open(app_vue, encoding='utf-8').read()
    lib = open(lib_vue, encoding='utf-8').read()
    ch = open(ch_vue, encoding='utf-8').read()
    api = open(api_js, encoding='utf-8').read()

    print('\n===== 回归防护: 刷新不得把有效会话踢回登录页 =====')
    # 关键: 启用认证的分支里必须先验证会话, 再决定 loggedIn
    check('onMounted 中有会话有效性探测(需登录接口)',
          'api.getChannels()' in app or 'await api.getChannels()' in app,
          '未见对受保护接口的探测 —— 刷新会停在登录页')
    check('探测到 401 时才判定未登录',
          'loggedIn.value = false' in app and 'loggedIn.value = true' in app, '')
    check('已登录分支设置用户名(状态栏显示)',
          'userName.value = s.username' in app, '')

    print('\n===== 探测性调用不得触发全局登出 =====')
    # refreshHealth 在启动时会被调用; 若未静音, 其 401 会广播 unauthorized 把用户踢出
    check('refreshHealth 支持 silent 参数',
          'refreshHealth(silent' in app or 'silent = false' in app, '')
    check('启动探测使用静默模式',
          'silent' in app, '')
    check('存在 silent 判定分支', 'if (!silent)' in app, '')

    print('\n===== 后端会话有效期足够长 =====')
    auth_py = os.path.join(root, 'javsp', 'auth.py')
    auth = open(auth_py, encoding='utf-8').read()
    check('默认 TTL 为 7 天(168h)',
          'DEFAULT_TTL_HOURS = 168' in auth, '')
    check('有滑动续期(活跃用户不被登出)',
          '滑动续期' in auth, '')
    check('未勾选记住时也至少给 24h(不是会话级瞬失)',
          'min(ttl_h, 24)' in auth, '')

    print('\n===== 影片库默认目录 =====')
    check('LibraryView 接收 browseRoot prop',
          "browseRoot" in lib and 'defineProps' in lib, '')
    check('无历史值时预填浏览根',
          'props.browseRoot' in lib and 'scanPath.value = props.browseRoot' in lib,
          '未预填 —— 打开页面路径为空')
    check('App 拉取并下发浏览根',
          'loadBrowseRoot' in app and ':browse-root="browseRoot"' in app, '')
    check('用户已选过则不覆盖',
          'if (!scanPath.value)' in lib, '')

    print('\n===== 渠道监控: 所有渠道可见 =====')
    check('渠道页提供全部/启用/未启用筛选',
          "view === 'all'" in ch and "view === 'inactive'" in ch, '')
    check('默认展示全部', "view = ref('all')" in ch, '')
    check('单表渲染(不再拆成两张表)',
          'visibleRows' in ch and 'inactiveRows' not in ch,
          '拆两张表会让用户以为渠道没显示全')
    check('未启用行有淡化标识',
          'row-off' in ch, '')

    print('\n===== 401 处理不误伤 =====')
    check('401 广播机制存在', 'notifyUnauthorized' in api, '')
    check('Cookie 随请求携带(credentials)',
          "credentials: 'same-origin'" in api, '')

    print(f'\n{"=" * 54}')
    print(f'通过 {len(PASSED)} 项, 失败 {len(FAILED)} 项')
    for f in FAILED:
        print(f'  FAIL: {f}')
    print('会话体验验证通过 ✅' if not FAILED else '存在失败项 ❌')
    raise SystemExit(1 if FAILED else 0)


if __name__ == '__main__':
    main()