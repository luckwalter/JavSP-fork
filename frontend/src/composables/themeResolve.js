/**
 * 主题配置解析(纯函数, 不依赖 Vue, 便于在 Node 下直接单测)
 *
 * 为什么要服务端为准(issue #7):
 *   主题原先只存在浏览器 localStorage —— 换设备、换访问地址
 *   (localhost vs NAS IP vs 反代域名)、桌面版 profile 丢失,
 *   都会静默退回浅色, 表现为「浅色和深色不能保存, 每次打开都是浅色」。
 *   改为入库(config.yml 的 web 段)后由服务端统一裁决, localStorage 只作首帧缓存。
 */

/** 合法的四种主题模式 */
export const THEME_MODES = ['light', 'dark', 'auto', 'schedule']

/** 定时切换的默认时段 */
export const DEFAULT_SCHEDULE = { start: '19:00', end: '07:00' }

/**
 * 从服务端配置解析出应采用的主题
 *
 * @param {object} web 服务端 config.web 段(可能为空、字段可能缺失)
 * @param {object} current 当前本地状态 { mode, schedule }
 * @returns {object|null} 有变化时返回 { mode, schedule }; 无变化返回 null
 */
export function resolveThemeFromConfig(web, current) {
  if (!web || typeof web !== 'object') return null
  const mode = web.theme_mode
  const start = web.theme_schedule_start
  const end = web.theme_schedule_end
  // 非法/缺失的 mode 一律保持本地值, 不因脏数据把界面搞乱
  const nextMode = mode && THEME_MODES.includes(mode) ? mode : current.mode
  const nextSchedule = (start && end)
    ? { start, end }
    : { ...DEFAULT_SCHEDULE, ...current.schedule }
  if (nextMode === current.mode
    && nextSchedule.start === current.schedule.start
    && nextSchedule.end === current.schedule.end) {
    return null
  }
  return { mode: nextMode, schedule: nextSchedule }
}
