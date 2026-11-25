/**
 * themeResolve 纯函数单测(Node 直跑, 无需浏览器)
 *
 * 运行: cd frontend && node test/themeResolve.test.mjs
 *
 * 覆盖 issue #7 的核心判定: 服务端配置为准、非法值不污染本地、时段可独立覆盖。
 */
import assert from 'node:assert/strict'

import {
  resolveThemeFromConfig,
  THEME_MODES,
  DEFAULT_SCHEDULE,
} from '../src/composables/themeResolve.js'

const cur = { mode: 'light', schedule: { start: '19:00', end: '07:00' } }

// 1) 服务端给了深色 -> 采用服务端
let r = resolveThemeFromConfig(
  { theme_mode: 'dark', theme_schedule_start: '19:00', theme_schedule_end: '07:00' }, cur)
assert.equal(r.mode, 'dark')
assert.deepEqual(r.schedule, { start: '19:00', end: '07:00' })

// 2) 非法 mode -> 保持本地值, 不返回变更
r = resolveThemeFromConfig({ theme_mode: 'rainbow' }, cur)
assert.equal(r, null)

// 3) web 段缺失 -> 无变更
r = resolveThemeFromConfig(undefined, cur)
assert.equal(r, null)
r = resolveThemeFromConfig(null, cur)
assert.equal(r, null)

// 4) 服务端 schedule 一并覆盖
r = resolveThemeFromConfig(
  { theme_mode: 'schedule', theme_schedule_start: '20:00', theme_schedule_end: '06:00' }, cur)
assert.equal(r.mode, 'schedule')
assert.deepEqual(r.schedule, { start: '20:00', end: '06:00' })

// 5) 与本地完全一致 -> 无变更(不必写回)
r = resolveThemeFromConfig(
  { theme_mode: 'light', theme_schedule_start: '19:00', theme_schedule_end: '07:00' }, cur)
assert.equal(r, null)

// 6) 只改时段 -> mode 保持不变
r = resolveThemeFromConfig(
  { theme_mode: 'light', theme_schedule_start: '21:00', theme_schedule_end: '08:00' }, cur)
assert.equal(r.mode, 'light')
assert.deepEqual(r.schedule, { start: '21:00', end: '08:00' })

// 7) 服务端缺时段 -> 沿用本地时段
r = resolveThemeFromConfig({ theme_mode: 'dark' }, cur)
assert.equal(r.mode, 'dark')
assert.deepEqual(r.schedule, { start: '19:00', end: '07:00' })

// 常量自检
assert.deepEqual(THEME_MODES, ['light', 'dark', 'auto', 'schedule'])
assert.deepEqual(DEFAULT_SCHEDULE, { start: '19:00', end: '07:00' })

console.log('themeResolve 单测全部通过 ✅')
