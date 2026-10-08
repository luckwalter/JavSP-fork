"""单用户登录认证（会话 Cookie + PBKDF2 密码哈希）

==== 为什么不引第三方密码库 ====
刻意只用标准库(secrets/hashlib/hmac)实现, 不引 passlib/bcrypt/argon2:
  - passlib 已停止维护(bcrypt 后端也有版本兼容坑), 而认证是**安全根基**, 依赖越少越好;
  - PBKDF2-HMAC-SHA256 是 NIST 认可的密码派生算法, Python 标准库原生支持且性能足够;
  - 避免为单用户登录引入新的供应链面。

==== 安全设计(逐条对应 OWASP 认证常见缺陷)====
1. **不用明文比对**: 密码以 PBKDF2-HMAC-SHA256 + 随机盐存储, 校验用 `hmac.compare_digest`
   做**常量时间**比较(防时序侧信道)。注意: 前面还有一个"配置未启用"的短路分支, 那不涉及
   密码比对, 不构成时序泄漏。
2. **防暴力破解**: 连续失败达阈值即锁定一段时间, 锁定期间连正确密码也拒。
   计数与锁定状态存服务端(内存), 换进程/重启会重置 —— 对单用户自部署场景可接受,
   且避免了"计数器写在客户端被随意清零"的经典漏洞。
3. **会话令牌**: 32 字节 `secrets.token_urlsafe` 随机串; 服务端存令牌哈希(存 sha256
   而非明文, 万一内存被 dump 也不至于直接可用的会话); Cookie 设 HttpOnly(JS 读不到,
   防 XSS 窃取) + SameSite=Lax(防 CSRF)。
4. **会话过期**: 默认 7 天, 滑动续期(每次用到就顺延, 让活跃用户不被登出)。
   **会话落盘**: 会话表会写到磁盘(默认在配置文件同目录的 `.sessions.json`), 进程/容器
   重启后已登录的浏览器不会被踢下线 —— 否则一次重启就等于"全员登出", 表现为
   「操作一会儿就提示未登录或会话已过期」(v0.2.2 实测根因)。落盘的是令牌**哈希**,
   不是令牌本身, 即便文件被读走也无法反推出可用 Cookie。
5. **不泄露信息**: 登录失败统一提示"用户名或密码错误", 不区分"用户不存在"与"密码错",
   避免账号枚举。锁定提示也只说"尝试过多", 不暴露剩余次数给攻击者之外的猜测逻辑。
6. **默认兼容**: 未设置 `JAVSP_AUTH_PASSWORD` 时认证**整体禁用**, 13 个接口照旧放行,
   保证既有部署(如 0.2.1 NAS)升级后不会被突然锁在门外。

==== 环境变量 ====
    JAVSP_AUTH_PASSWORD   设置则启用登录; 留空/未设= 禁用认证
    JAVSP_AUTH_USERNAME   用户名(默认 admin)
    JAVSP_AUTH_TTL_HOURS  会话有效期(默认 168 = 7 天)
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from typing import Dict, Optional


logger = logging.getLogger(__name__)

# ---- 参数(默认值可经环境变量覆盖) ----
FAILURE_THRESHOLD = 5           # 连续失败多少次后锁定
LOCKOUT_SECONDS = 300# 锁定时长(秒) = 5 分钟
PBKDF2_ITERATIONS = 260_000     # OWASP 2023 推荐值(平衡安全与登录耗时)
DEFAULT_TTL_HOURS = 168         # 会话 7 天
SESSION_COOKIE = 'javsp_session'

_lock = threading.Lock()
# 令牌哈希 -> 会话信息。存哈希不存明文, 内存泄露也无法直接冒用会话
_sessions: Dict[str, dict] = {}
# 单用户暴力破解状态
_fail_state = {'fails': 0, 'locked_until': 0.0}
# 启动时的密码摘要(只在启用认证时计算一次, 之后不再碰明文)
_pwd_digest: Optional[bytes] = None
_pwd_salt: Optional[bytes] = None
_auth_enabled = False


def _read_config():
    """从环境变量读取配置, 计算密码摘要(模块导入时执行一次)"""
    global _auth_enabled, _pwd_digest, _pwd_salt
    password = os.getenv('JAVSP_AUTH_PASSWORD', '')
    if not password:
        _auth_enabled = False
        logger.info('认证未启用(未设置 JAVSP_AUTH_PASSWORD), 所有接口维持免登录')
        return
    _pwd_salt = secrets.token_bytes(16)
    _pwd_digest = hashlib.pbkdf2_hmac(
        'sha256', password.encode('utf-8'), _pwd_salt, PBKDF2_ITERATIONS)
    _auth_enabled = True
    logger.info(f'认证已启用(用户: {get_username()}), 密码以 PBKDF2-SHA256 '
                f'{PBKDF2_ITERATIONS} 轮存储')


def get_username() -> str:
    return os.getenv('JAVSP_AUTH_USERNAME', 'admin') or 'admin'


def is_enabled() -> bool:
    """认证是否启用。未启用时上层应完全放行(向后兼容)"""
    return _auth_enabled


def _verify_password(candidate: str) -> bool:
    """常量时间校验密码"""
    if not _auth_enabled or _pwd_digest is None or _pwd_salt is None:
        return False
    got = hashlib.pbkdf2_hmac(
        'sha256', candidate.encode('utf-8'), _pwd_salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(got, _pwd_digest)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def lockout_remaining() -> float:
    """当前锁定剩余秒数, 0=未锁定"""
    with _lock:
        remain = _fail_state['locked_until'] - time.time()
        return max(0.0, remain)


def login(username: str, password: str, remember: bool = False) -> dict:
    """尝试登录。返回 {'ok': bool, 'token': str|None, 'message': str}

    失败信息刻意统一(不区分用户不存在/密码错误), 防账号枚举。
    """
    if not _auth_enabled:
        # 未启用认证时, 视为已登录 —— 这样前端无需为"关闭认证"单独写一套流程
        return {'ok': True, 'token': None, 'message': '认证未启用'}
    if lockout_remaining() > 0:
        return {'ok': False, 'token': None, 'message': '尝试次数过多, 已临时锁定'}

    ok = hmac.compare_digest(username or '', get_username()) and _verify_password(password or '')
    new_token = None
    with _lock:
        if ok:
            _fail_state['fails'] = 0
            _fail_state['locked_until'] = 0.0
            token = secrets.token_urlsafe(32)
            ttl_h = DEFAULT_TTL_HOURS
            try:
                ttl_h = int(os.getenv('JAVSP_AUTH_TTL_HOURS', str(DEFAULT_TTL_HOURS)))
            except ValueError:
                pass
            if not remember:
                ttl_h = min(ttl_h, 24)   # 不记住: 仅本次浏览器会话内有效
            ttl = ttl_h * 3600
            now = time.time()
            _sessions[_token_hash(token)] = {'created': now, 'expires': now + ttl, 'ttl': ttl}
            new_token = token
            logger.info(f'登录成功: {username}')
        else:
            # 失败计数
            _fail_state['fails'] += 1
            if _fail_state['fails'] >= FAILURE_THRESHOLD:
                _fail_state['locked_until'] = time.time() + LOCKOUT_SECONDS
                logger.warning(f'登录失败次数达阈值, 锁定 {LOCKOUT_SECONDS}s')
            logger.warning(f'登录失败: {username}(第{_fail_state["fails"]}次)')
    # 落盘放到锁外: 文件 IO 不该占着会话锁(否则并发请求会在这里排队)
    if new_token:
        _save_state()
        return {'ok': True, 'token': new_token, 'message': '登录成功'}
    return {'ok': False, 'token': None, 'message': '用户名或密码错误'}


def verify_token(token: Optional[str]) -> bool:
    """校验会话令牌, 有效则续期(滑动)。无效/过期返回 False"""
    if not _auth_enabled:
        return True               # 未启用 -> 放行
    if not token:
        return False
    th = _token_hash(token)
    now = time.time()
    renewed = False
    with _lock:
        s = _sessions.get(th)
        if not s:
            return False
        if s['expires'] < now:
            _sessions.pop(th, None)
            renewed = True       # 会话表变了, 需要同步落盘
            ok = False
        else:
            # 滑动续期: 剩余不足一半时延到满, 避免活跃用户被莫名登出。
            # 用会话自带的 ttl 而不是 (expires-created) —— 后者每次续期都会变大,
            # 会让有效期被无限"滚雪球"放大(原实现的缺陷)。
            ttl = s.get('ttl') or (s['expires'] - s['created'])
            if s['expires'] - now < ttl / 2:
                s['expires'] = now + ttl
                renewed = True
            ok = True
    if renewed:
        _save_state()
    return ok


def logout(token: Optional[str]) -> None:
    if token and _auth_enabled:
        with _lock:
            _sessions.pop(_token_hash(token), None)
        _save_state()


def prune_sessions() -> int:
    """清理过期会话, 返回清理条数(供 lifespan 定期调用, 防内存缓慢增长)"""
    now = time.time()
    with _lock:
        expired = [k for k, v in _sessions.items() if v['expires'] < now]
        for k in expired:
            _sessions.pop(k, None)
    if expired:
        _save_state()
    return len(expired)


def auth_status() -> dict:
    """给前端 GET /api/auth/status 用: 当前是否需要登录, 是否已登录, 锁定情况"""
    return {
        'enabled': _auth_enabled,
        'username': get_username() if _auth_enabled else None,
        'locked_for': round(lockout_remaining(), 1),
        'ttl_hours': DEFAULT_TTL_HOURS,
    }


# ---- 会话落盘 ----
# 为什么需要: 会话原本只存在进程内存里, 容器/进程一重启就全部蒸发, 而浏览器侧的
# Cookie 还在 —— 之后每次请求都被判为「未登录或会话已过期」, 且刷新后仍带旧 Cookie,
# 用户会卡在"怎么点都没用"的界面(v0.2.2 实测: 容器 17:09:45 重启, 之后用户请求全 401)。
# 落盘内容只有令牌哈希与过期时间, 即便文件被读到也无法反推出可用 Cookie。
_state_dir_override: Optional[str] = None


def set_state_dir(path: str) -> None:
    """由 server 在启动时指定会话文件目录 = **实际生效的配置文件所在目录**

    配置文件往往被挂载出来(如 /etc/javsp/config.yml), 那个卷才是重启后仍在的地方;
    包目录在容器重建时会随镜像还原, 不适合放需要跨重启保留的状态。
    """
    global _state_dir_override
    if path:
        _state_dir_override = str(path)
        # 🔑 必须按新路径**重新读一次**: 模块导入时已按默认路径加载过一遍,
        # 若不重载就会变成"从默认位置读、往指定位置写"(读写不同源), 重启后会话照样丢。
        _sessions.clear()
        _load_state()


def _state_path() -> str:
    env = os.getenv('JAVSP_SESSION_FILE', '').strip()
    if env:
        return env
    if _state_dir_override:
        return os.path.join(_state_dir_override, '.sessions.json')
    # 兜底顺序: /etc/javsp(常见挂载点) -> 包目录
    for d in ('/etc/javsp', os.path.dirname(os.path.abspath(__file__))):
        if os.path.isdir(d):
            return os.path.join(d, '.sessions.json')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), '.sessions.json')


def _load_state() -> None:
    """启动时恢复未过期的会话。任何异常都只记录不抛出: 读不到就当全新启动"""
    path = _state_path()
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        now = time.time()
        n = 0
        for th, s in (data.get('sessions') or {}).items():
            if isinstance(s, dict) and s.get('expires', 0) > now:
                _sessions[th] = {
                    'created': float(s.get('created', now)),
                    'expires': float(s['expires']),
                    'ttl': float(s.get('ttl') or (float(s['expires']) - float(s.get('created', now)))),
                }
                n += 1
        if n:
            logger.info(f'已从磁盘恢复 {n} 个未过期会话')
    except Exception as e:      # noqa: BLE001
        logger.warning(f'会话文件读取失败(按全新启动处理): {e}')


def _save_state() -> None:
    """把当前会话写回磁盘。失败只告警 —— 落盘只是增强, 不该让登录本身失败"""
    path = _state_path()
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'sessions': _sessions}, f)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)   # 仅属主可读(会话哈希亦不应外泄)
        except OSError:
            pass
    except Exception as e:      # noqa: BLE001
        logger.warning(f'会话落盘失败(不影响本次登录): {e}')


# 模块导入时读取配置(密码只在启动时进内存一次, 之后不保留明文)
_read_config()
_load_state()
