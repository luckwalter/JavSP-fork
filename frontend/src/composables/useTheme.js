/**
 * 主题管理: 浅色 / 深色 / 跟随系统 / 定时切换
 * ---------------------------------------------------------------------------
 * 为什么需要: 夜间整理影片时全站白底刺眼; 且「跟着系统走」对已经手动
 * 切过系统的用户不够——定时(如 19:00-07:00 自动深色)覆盖这部分场景。
 *
 * 设计取舍:
 *   - 模块级单例: StatusBar 切换器 / 设置页外观子页 / App 共享同一状态,
 *     首个调用者启动监听, 后续调用直接复用;
 *   - localStorage(跨会话) 而非 sessionStorage: 主题是长期偏好, 与
 *     usePersist 的「刷新可恢复、关页即清」语义不同;
 *   - 无 DOM 环境(单测/SSR)容错: 所有 DOM/localStorage 访问都带降级;
 *   - apply 用 dataset.theme 驱动, CSS 侧 [data-theme="dark"] 覆盖令牌,
 *     组件零改动即可换肤。
 */
import { ref, computed, watch } from 'vue'

const STORAGE_MODE = 'javsp:theme-mode'
const STORAGE_SCHEDULE = 'javsp:theme-schedule'
export const THEME_MODES = ['light', 'dark', 'auto', 'schedule']
export const DEFAULT_SCHEDULE = { start: '19:00', end: '07:00' }

/** 读取 JSON 存储, 脏数据/隐私模式一律回退默认值 */
function readStorage(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : fallback
  } catch (_) {
    return fallback
  }
}

function writeStorage(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch (_) {
    // 隐私模式/配额满: 主题是增强项, 不能因此中断主流程
  }
}

/** 「HH:MM」→ 当日分钟数; 非法输入回退 fallback */
function toMinutes(hhmm, fallback) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm || '')
  if (!m) return fallback
  const h = Math.min(23, Math.max(0, Number(m[1])))
  const min = Math.min(59, Math.max(0, Number(m[2])))
  return h * 60 + min
}

/** 当前时刻是否落在定时区间内; 支持跨午夜(如 19:00 → 次日 07:00) */
export function inSchedule(schedule, now = new Date()) {
  const start = toMinutes(schedule.start, toMinutes(DEFAULT_SCHEDULE.start, 0))
  const end = toMinutes(schedule.end, toMinutes(DEFAULT_SCHEDULE.end, 0))
  const cur = now.getHours() * 60 + now.getMinutes()
  return start <= end ? cur >= start && cur < end : cur >= start || cur < end
}

/* ---------- 模块级单例状态 ---------- */
const mode = ref(readStorage(STORAGE_MODE, 'light'))
const schedule = ref({ ...DEFAULT_SCHEDULE, ...readStorage(STORAGE_SCHEDULE, {}) })
const systemDark = ref(false)
const tick = ref(0) // 定时模式每分钟 +1, 驱动 resolvedDark 重算

/** 实际生效是否为深色(供模板/测试读取) */
const resolvedDark = computed(() => {
  if (mode.value === 'dark') return true
  if (mode.value === 'light') return false
  if (mode.value === 'auto') return systemDark.value
  if (mode.value === 'schedule') {
    void tick.value // 建立依赖: 每分钟重算
    return inSchedule(schedule.value)
  }
  return false
})

/** 把 resolvedDark 写到根元素; 切换瞬间禁过渡, 防全站属性一起渐变闪烁 */
function apply() {
  const root = document.documentElement
  const next = resolvedDark.value ? 'dark' : 'light'
  if (root.dataset.theme === next) return
  root.classList.add('theme-switching')
  root.dataset.theme = next
  // 双 rAF: 确保「无过渡」帧已渲染后再恢复过渡
  requestAnimationFrame(() => {
    requestAnimationFrame(() => root.classList.remove('theme-switching'))
  })
}

let started = false

function ensureStarted() {
  if (started) return
  started = true

  // 跟随系统: 监听 prefers-color-scheme
  if (typeof window.matchMedia === 'function') {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    systemDark.value = mq.matches
    const onMedia = (e) => { systemDark.value = e.matches }
    if (mq.addEventListener) mq.addEventListener('change', onMedia)
    else if (mq.addListener) mq.addListener(onMedia) // 旧 Safari
  }

  // 定时模式: 每分钟对齐分钟边界重算
  setInterval(() => { tick.value++ }, 60_000)

  // 任何输入变化都重新 apply(immediate: 首帧即应用, 供挂载前防闪烁)
  watch([mode, schedule, systemDark, tick], apply, { immediate: true })
}

function setMode(m) {
  if (!THEME_MODES.includes(m)) return
  mode.value = m
  writeStorage(STORAGE_MODE, m)
}

function setSchedule(s) {
  const next = {
    start: s.start || DEFAULT_SCHEDULE.start,
    end: s.end || DEFAULT_SCHEDULE.end,
  }
  schedule.value = next
  writeStorage(STORAGE_SCHEDULE, next)
}

/**
 * 主题入口。多处调用共享同一单例; 首次调用即完成
 * 「挂载前防闪烁 apply + 监听器启动」。
 */
export function useTheme() {
  ensureStarted()
  return { mode, schedule, resolvedDark, setMode, setSchedule }
}
