/**
 * UI 状态持久化 (两级)
 * ---------------------------------------------------------------------------
 * 为什么需要: 旧版刷新页面后扫描结果/目录/队列全部丢失, 用户无法接续上次进度 ——
 * 这是诊断出的三大体验断点之一。
 *
 * 设计取舍:
 *   - 会话级(persistState/saveState) 存 sessionStorage: 关闭标签页即清理, 避免
 *     "下次打开还是一周前的旧结果"造成的误解; 刷新页面仍可恢复。
 *   - 长期级(persistLocal/saveLocal) 存 localStorage: 用于用户明确的操作偏好
 *     (如批量整理的确认方式), 需要跨会话保持 —— 这类偏好不是"临时结果"。
 *   - 统一前缀 + JSON 容错: 读到脏数据时返回默认值, 而不是让整个页面崩掉。
 */
const PREFIX = 'javsp:'

function readFrom(storage, key, defaultValue) {
  const storageKey = PREFIX + key
  let data
  try {
    const raw = storage.getItem(storageKey)
    data = raw ? JSON.parse(raw) : defaultValue
  } catch (_) {
    data = defaultValue
  }
  if (data === null || data === undefined) return defaultValue
  // 类型一致性校验: 存的内容与默认值类型不符(旧版本残留/别的页面写过同键/
  // 用户手改过存储)时回退默认值 —— 否则 movies 可能是字符串或
  // 数字, 模板 v-for 直接抛 TypeError 把整个页面白屏。
  const sameArray = Array.isArray(data) === Array.isArray(defaultValue)
  const sameKind = sameArray && (typeof data === 'object'
    ? typeof defaultValue === 'object'
    : typeof data === typeof defaultValue)
  if (!sameKind) return defaultValue
  return data
}

function writeTo(storage, key, value) {
  try {
    storage.setItem(PREFIX + key, JSON.stringify(value))
  } catch (_) {
    // 隐私模式/配额满时静默失败: 持久化是增强项, 不能因此中断主流程
  }
}

export function persistState(key, defaultValue) {
  return readFrom(sessionStorage, key, defaultValue)
}

export function saveState(key, value) {
  writeTo(sessionStorage, key, value)
}

export function clearState(key) {
  try {
    sessionStorage.removeItem(PREFIX + key)
  } catch (_) {
    /* 同上 */
  }
}

/** 长期偏好: 跨标签页/跨会话保持(用户明确表达过的操作偏好) */
export function persistLocal(key, defaultValue) {
  return readFrom(localStorage, key, defaultValue)
}

export function saveLocal(key, value) {
  writeTo(localStorage, key, value)
}
