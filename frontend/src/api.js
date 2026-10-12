// 后端 API 封装
const BASE = ''

// ---- 全局 401 处理 ----
// 后端中间件对未登录请求统一返回 401。这里用事件广播让 App 切到登录页,
// 而不是在每个调用点各写一遍判断 —— 漏一处就会出现"点了没反应"。
// 用 EventTarget 而非直接引入 Vue, 保持 api.js 与框架解耦。
export const authEvents = new EventTarget()

function notifyUnauthorized() {
  authEvents.dispatchEvent(new CustomEvent('unauthorized'))
}

// 包装 fetch: 统一带上凭证(Cookie 由浏览器自动携带, same-origin),
// 并在 401 时广播。credentials:'same-origin' 是默认值, 显式写出以便理解意图。
export async function request(path, options = {}) {
  const r = await fetch(BASE + path, { credentials: 'same-origin', ...options })
  if (r.status === 401) {
    notifyUnauthorized()
    throw new Error('未登录或会话已过期')
  }
  return r
}

// 统一解析后端错误体: FastAPI 的 detail 可能是字符串, 也可能是 422 校验错误
// 的**数组**(此时直接 String(detail) 会变成 '[object Object]', 真实原因丢失);
// 响应还可能是反向代理的 HTML 错误页(非 JSON)。这里一并归一化。
function normalizeDetail(detail) {
  if (detail === undefined || detail === null) return ''
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (!d || typeof d !== 'object') return String(d)
        const loc = Array.isArray(d.loc) ? d.loc.join('.') : ''
        return (loc ? loc + ': ' : '') + (d.msg || JSON.stringify(d))
      })
      .join('; ')
  }
  if (typeof detail === 'object') return JSON.stringify(detail)
  return String(detail)
}

// 读取错误响应体, 任何解析失败都退化为空对象(而非抛 SyntaxError 掩盖真实原因)
async function readDetail(r, fallback) {
  try {
    const j = await r.json()
    return normalizeDetail(j.detail) || fallback
  } catch (_) {
    return fallback
  }
}

export async function scan(path) {
  const r = await request('/api/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  if (!r.ok) throw new Error(await readDetail(r, 'scan failed'))
  return r.json()
}

// ⚠️ 下面这些只读接口**也必须走 request()**, 不能用裸 fetch。
// 裸 fetch 拿不到 401 广播 -> App 收不到"会话已失效"的信号 -> 界面停在错误提示上
// 动不了, 所有按钮点了都没反应(v0.2.2 实测反馈: 设置页报"未登录或会话已过期",
// 但既没跳登录页也没任何后续反应, 用户只能手动刷新碰运气)。
export async function getMovies() {
  const r = await request('/api/movies')
  if (!r.ok) throw new Error(await readDetail(r, '读取影片列表失败'))
  return r.json()
}

// 列举目录下的子目录(不列文件), 供目录选择器逐级导航
export async function browse(path) {
  const r = await request('/api/browse?path=' + encodeURIComponent(path || '/'))
  if (!r.ok) throw new Error(await readDetail(r, '读取目录失败'))
  return r.json()
}

// 健康检查(同时用于获取服务端版本号, 单一版本源在后端 pyproject.toml)
export async function getHealth() {
  const r = await request('/api/health')
  if (!r.ok) throw new Error('health check failed')
  return r.json()
}

export async function getConfig() {
  const r = await request('/api/config')
  if (!r.ok) throw new Error(await readDetail(r, 'config get failed'))
  return r.json()
}

// 运行时实际生效的配置（区别于 /api/config 的磁盘值），含封面裁剪引擎与依赖可用性
export async function getConfigRuntime() {
  const r = await request('/api/config/runtime')
  if (!r.ok) throw new Error(await readDetail(r, 'config runtime get failed'))
  return r.json()
}

// 各刮削渠道的健康档案与熔断状态（覆盖全部已注册源，含未启用的）
export async function getChannels() {
  const r = await request('/api/channels')
  if (!r.ok) throw new Error(await readDetail(r, 'channels get failed'))
  return r.json()
}

// 立即对全部启用渠道做一次主动探活（正常无需手动调，后台会按周期自动探活）
export async function checkChannels() {
  const r = await request('/api/channels/check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: 'null',
  })
  if (!r.ok) throw new Error(await readDetail(r, 'channels check failed'))
  return r.json()
}

export async function putConfig(cfg) {
  // 必须走 request(): 它显式带 credentials 并统一处理 401。
  // 原实现用裸 fetch —— 带 body 的 PUT 在跨源判定上更严格, 会漏掉会话 Cookie,
  // 表现为「保存设置时提示未登录或会话已过期」(v0.2.2 实测反馈)。
  const r = await request('/api/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  })
  if (!r.ok) throw new Error(await readDetail(r, 'config put failed'))
  return r.json()
}

// 消费 SSE 流(POST + ReadableStream), onEvent 收到每个 data 事件
export function scrapeStream(payload, onEvent) {
  return consumeSSE(BASE + '/api/scrape', payload, onEvent)
}

export function organizeStream(guid, onEvent) {
  return consumeSSE(BASE + '/api/organize', { guid }, onEvent)
}

export function batchStream(payload, onEvent) {
  return consumeSSE(BASE + '/api/batch', payload, onEvent)
}

// SSE 读取超时: 服务端在长任务里会持续发事件, 但也可能因为网络/代理问题静默挂住。
// 没有这个上限时 reader.read() 可无限挂起, 界面永远停在"运行中"。
const SSE_IDLE_TIMEOUT_MS = 120000

function consumeSSE(url, body, onEvent, opts = {}) {
  // 不用 `new Promise(async ...)`(async executor 反模式: executor 内抛出的异常
  // 不会正确 reject 到 Promise), 改为同步 executor + 内部 async 函数。
  return new Promise((resolve, reject) => {
    let settled = false
    const done = (fn, v) => {
      if (settled) return
      settled = true
      fn(v)
    }
    const controller = new AbortController()
    if (opts.signal) {
      opts.signal.addEventListener('abort', () => controller.abort())
    }
    ;(async () => {
      try {
        const resp = await fetch(url, {
          method: 'POST',
          // SSE 的三个入口(单部刮削/批量刮削/整理)都经这里。带 body 的 POST
          // 在跨源判定上比 GET 更严格, **必须显式带 credentials**, 否则会丢会话 Cookie
          // -> 后端 401 -> 前端报"未登录或会话已过期"。v0.2.2 实测踩过(putConfig 同因)。
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          signal: controller.signal,
        })
        if (resp.status === 401) {
          notifyUnauthorized()
          throw new Error('未登录或会话已过期')
        }
        if (!resp.ok) {
          const e = await resp.json().catch(() => ({}))
          throw new Error(normalizeDetail(e.detail) || 'request failed')
        }
        const reader = resp.body.getReader()
        const decoder = new TextDecoder()
        let buf = ''
        while (true) {
          // 空闲超时: 只有在超时窗口内一个字节都没收到才判为挂死
          let timerId = null
          const timer = new Promise((_, rej) => {
            timerId = setTimeout(
              () => rej(new Error('SSE 读取超时, 请检查服务是否仍在运行')),
              SSE_IDLE_TIMEOUT_MS
            )
          })
          let res
          try {
            res = await Promise.race([reader.read(), timer])
          } finally {
            if (timerId) clearTimeout(timerId)
          }
          const { done: streamDone, value } = res
          if (streamDone) break
          buf += decoder.decode(value, { stream: true })
          let idx
          while ((idx = buf.indexOf('\n\n')) >= 0) {
            const chunk = buf.slice(0, idx)
            buf = buf.slice(idx + 2)
            const line = chunk.split('\n').find((l) => l.startsWith('data: '))
            if (line) {
              try {
                const data = JSON.parse(line.slice(6))
                onEvent(data)
              } catch (_) {
                /* 忽略单行解析错误 */
              }
            }
          }
        }
        try { reader.releaseLock() } catch (_) { /* 已释放 */ }
        // 正常读完也 resolve: 上游可能没发终止事件(如代理截断), 调用方应能正常收尾
        done(resolve, undefined)
      } catch (e) {
        done(reject, e)
      }
    })()
  })
}

// ---------------- 认证 ----------------

/** 查询认证状态: 前端启动时先问这个, 决定是否显示登录页 */
export async function getAuthStatus() {
  const r = await request('/api/auth/status')
  return r.json()
}

/** 登录。成功返回 true; 失败抛出带 detail 与 lockedFor 的错误 */
export async function login(username, password, remember) {
  const r = await fetch(BASE + '/api/auth/login', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, remember }),
  })
  if (r.ok) return r.json()
  const detail = await readDetail(r, '登录失败')
  const err = new Error(detail)
  // 423 = 被锁定(区别于 401 凭据错误), 前端据此切换到倒计时态
  err.lockedFor = r.status === 423 ? (await authLockRemaining()) : 0
  throw err
}

/** 锁定期内服务端会返回 423, 但登录响应体里没有剩余秒数, 需再问一次状态 */
export async function authLockRemaining() {
  try {
    const s = await getAuthStatus()
    return s.locked_for || 0
  } catch (_) {
    return 300
  }
}

export async function logout() {
  const r = await fetch(BASE + '/api/auth/logout', {
    method: 'POST',
    credentials: 'same-origin',
  })
  return r.ok ? r.json() : { ok: false }
}
