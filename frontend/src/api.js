// 后端 API 封装
const BASE = ''

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
  const r = await fetch(BASE + '/api/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  if (!r.ok) throw new Error(await readDetail(r, 'scan failed'))
  return r.json()
}

export async function getMovies() {
  const r = await fetch(BASE + '/api/movies')
  return r.json()
}

// 列举目录下的子目录(不列文件), 供目录选择器逐级导航
export async function browse(path) {
  const r = await fetch(BASE + '/api/browse?path=' + encodeURIComponent(path || '/'))
  if (!r.ok) throw new Error(await readDetail(r, '读取目录失败'))
  return r.json()
}

// 健康检查(同时用于获取服务端版本号, 单一版本源在后端 pyproject.toml)
export async function getHealth() {
  const r = await fetch(BASE + '/api/health')
  if (!r.ok) throw new Error('health check failed')
  return r.json()
}

export async function getConfig() {
  const r = await fetch(BASE + '/api/config')
  if (!r.ok) throw new Error(await readDetail(r, 'config get failed'))
  return r.json()
}

// 运行时实际生效的配置（区别于 /api/config 的磁盘值），含封面裁剪引擎与依赖可用性
export async function getConfigRuntime() {
  const r = await fetch(BASE + '/api/config/runtime')
  if (!r.ok) throw new Error(await readDetail(r, 'config runtime get failed'))
  return r.json()
}

export async function putConfig(cfg) {
  const r = await fetch(BASE + '/api/config', {
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
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          signal: controller.signal,
        })
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
