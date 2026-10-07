// 后端 API 封装
const BASE = ''

export async function scan(path) {
  const r = await fetch(BASE + '/api/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  if (!r.ok) throw new Error((await r.json()).detail || 'scan failed')
  return r.json()
}

export async function getMovies() {
  const r = await fetch(BASE + '/api/movies')
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
  if (!r.ok) throw new Error((await r.json()).detail || 'config get failed')
  return r.json()
}

// 运行时实际生效的配置（区别于 /api/config 的磁盘值），含封面裁剪引擎与依赖可用性
export async function getConfigRuntime() {
  const r = await fetch(BASE + '/api/config/runtime')
  if (!r.ok) throw new Error((await r.json()).detail || 'config runtime get failed')
  return r.json()
}

export async function putConfig(cfg) {
  const r = await fetch(BASE + '/api/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  })
  if (!r.ok) throw new Error((await r.json()).detail || 'config put failed')
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

function consumeSSE(url, body, onEvent) {
  return new Promise(async (resolve, reject) => {
    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!resp.ok) {
        const e = await resp.json().catch(() => ({}))
        throw new Error(e.detail || 'request failed')
      }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
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
      resolve()
    } catch (e) {
      reject(e)
    }
  })
}
