<script setup>
/** 底部常驻状态栏
 *  设计意图: 渠道熔断是「后台发生但影响前台」的事 —— 用户不该为此专门切页面。
 *  放在常驻位置, 一眼看到「有N 个源被跳过」, 不必进监控页排查。
 */
import { computed } from 'vue'
import { useTheme, THEME_MODES } from '../composables/useTheme'

const props = defineProps({
  version: { type: String, default: '' },
  health: { type: Object, default: () => ({}) },
  task: { type: Object, default: () => ({}) },
  user: { type: String, default: '' },
})

const emit = defineEmits(['logout', 'navigate'])

const summary = computed(() => props.health || {})
const active = computed(() => summary.value.active || 0)
const ok = computed(() => summary.value.ok || 0)
const tripped = computed(() => summary.value.tripped || 0)

const healthText = computed(() => {
  if (!active.value) return '渠道状态加载中'
  if (tripped.value > 0) return `${tripped.value} 个渠道已跳过`
  if (ok.value > 0) return `${ok.value}/${active.value} 渠道正常`
  return `${active.value} 个渠道待检查`
})

const healthClass = computed(() => (tripped.value > 0 ? 'pill--danger' : ok.value > 0 ? 'pill--success' : 'pill--muted'))

const taskRunning = computed(() => props.task && props.task.running)
const pct = computed(() => {
  const t = props.task || {}
  if (!t.total) return 0
  return Math.round(((t.index || 0) / t.total) * 100)
})

/* ---------- 主题快捷切换: 点一下循环 浅色→深色→自动→定时 ----------
   详细配置(定时时段)在设置页「外观」子页。 */
const { mode, schedule, setMode } = useTheme()

const themeLabel = computed(() => {
  return { light: '浅色', dark: '深色', auto: '自动', schedule: '定时' }[mode.value] || '浅色'
})

const themeTitle = computed(() => {
  const tip = {
    light: '主题: 浅色(点击切换到深色)',
    dark: '主题: 深色(点击切换到跟随系统)',
    auto: '主题: 跟随系统(点击切换到定时)',
    schedule: `主题: 定时 ${schedule.value.start}-${schedule.value.end} 自动切换(点击切换到浅色)`,
  }
  return tip[mode.value] || tip.light
})

function cycleTheme() {
  const i = THEME_MODES.indexOf(mode.value)
  setMode(THEME_MODES[(i + 1) % THEME_MODES.length])
}
</script>

<template>
  <footer class="statusbar">
    <div class="statusbar-left">
      <span class="pill pill--dot" :class="healthClass" :title="`启用 ${active} 个渠道`">
        {{ healthText }}
      </span>
      <button v-if="tripped > 0" class="link-btn" @click="$emit('navigate', 'channels')">
        查看原因
      </button>
    </div>

    <div v-if="taskRunning" class="statusbar-task">
      <span class="statusbar-task-text">
        第 {{ task.index }}/{{ task.total }} 部 · {{ task.current || '处理中' }}
      </span>
      <div class="progress statusbar-progress">
        <div class="progress-bar" :style="{ width: pct + '%' }"></div>
      </div>
    </div>

    <div class="statusbar-right">
      <button class="link-btn theme-btn" :title="themeTitle" :aria-label="themeTitle"
              @click="cycleTheme">{{ themeLabel }}</button>
      <span v-if="user" class="statusbar-user">{{ user }}</span>
      <button v-if="user" class="link-btn" @click="emit('logout')">退出</button>
      <span class="statusbar-version">v{{ version || '—' }}</span>
      <!-- 下个版本预告标记: 例行 bug 修复 + 软件更新检查功能 -->
      <span class="statusbar-next" title="下个版本将进行例行 bug 修复, 并新增软件更新检查功能">下版预告</span>
    </div>
  </footer>
</template>

<style scoped>
.statusbar {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  flex-shrink: 0;
  min-height: var(--statusbar-height);
  padding: 0 var(--space-5);
  background: var(--color-bg-surface);
  border-top: 1px solid var(--color-border-light);
  font-size: var(--font-sm);
}

.statusbar-left { display: flex; align-items: center; gap: var(--space-3); }

.statusbar-task {
  flex: 1;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  max-width: 420px;
}

.statusbar-task-text {
  white-space: nowrap;
  color: var(--color-text-secondary);
}

.statusbar-progress { flex: 1; min-width: 80px; }

.statusbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-left: auto;
}

.statusbar-user { color: var(--color-text-secondary); }
.statusbar-version { color: var(--color-text-tertiary); font-variant-numeric: tabular-nums; }

/* 下版预告标记: 低调但可见, 悬停有说明 */
.statusbar-next {
  color: var(--color-text-tertiary);
  font-size: var(--font-sm);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm, 4px);
  padding: 0 0.4em;
  cursor: default;
}

/* 主题按钮: 固定 2 字宽, 四种模式标签都是 2 字, 切换时宽度不抖动 */
.theme-btn { min-width: 2em; text-align: center; }

.link-btn {
  padding: 0;
  font-family: inherit;
  font-size: var(--font-sm);
  color: var(--color-primary);
  background: none;
  border: 0;
  cursor: pointer;
}

.link-btn:hover { text-decoration: underline; }
.link-btn:focus-visible { outline: none; box-shadow: var(--focus-ring); border-radius: var(--radius-sm); }

@media (max-width: 860px) {
  .statusbar { flex-wrap: wrap; padding: var(--space-2) var(--space-4); }
  .statusbar-task { max-width: none; order: 3; width: 100%; }
}
</style>
