/**
 * 会话级 UI 状态持久化
 * ---------------------------------------------------------------------------
 * 为什么需要: 旧版刷新页面后扫描结果/目录/队列全部丢失, 用户无法接续上次进度 ——
 * 这是诊断出的三大体验断点之一。
 *
 * 设计取舍:
 *   - 存 sessionStorage 而非 localStorage: 关闭标签页即清理, 避免"下次打开还是
 *     一周前的旧结果"造成的误解;刷新页面仍可恢复。
 *   - 统一前缀 + JSON 容错: 读到脏数据时返回默认值, 而不是让整个页面崩掉。
 */
const PREFIX = 'javsp:'

export function persistState(key, defaultValue) {
  const storageKey = PREFIX + key
  let data
  try {
    const raw = sessionStorage.getItem(storageKey)
    data = raw ? JSON.parse(raw) : defaultValue
  } catch (_) {
    data = defaultValue
  }
  if (data === null || data === undefined) return defaultValue
  return data
}

export function saveState(key, value) {
  try {
    sessionStorage.setItem(PREFIX + key, JSON.stringify(value))
  } catch (_) {
    // 隐私模式/配额满时静默失败: 持久化是增强项, 不能因此中断主流程
  }
}

export function clearState(key) {
  try {
    sessionStorage.removeItem(PREFIX + key)
  } catch (_) {
    /* 同上 */
  }
}
