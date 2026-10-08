<script setup>
/** 左侧主导航
 *  信息架构决策: 主工作流按使用顺序纵向排列(影片库→…→整理落盘),
 *  工具类(渠道监控/系统设置)沉到「工具」分组 —— 避免每屏都被诊断与设置项干扰。
 */
const props = defineProps({
  active: { type: String, required: true },
  tripCount: { type: Number, default: 0 },
})

const emit = defineEmits(['navigate'])

const WORKFLOW = [
  { key: 'library', label: '影片库', hint: '扫描与选择', icon: 'M3 5h18v14H3zM3 9h18' },
  { key: 'queue', label: '刮削队列', hint: '批量任务', icon: 'M4 6h16M4 12h16M4 18h10' },
  { key: 'review', label: '结果审阅', hint: '逐部核对', icon: 'M5 3l14 9-14 9V3z' },
  { key: 'lookup', label: '单部查询', hint: '临时刮削', icon: 'M11 11l4 4M10 6a4 4 0 110 8 4 4 0 010-8z' },
]

// 破坏性操作单独成组并用红色, 视觉上与其余入口明确区分
const DESTRUCTIVE = [
  { key: 'organize', label: '整理落盘', hint: '改名与移动', icon: 'M4 16h12M12 12l4 4-4 4', danger: true },
]

const TOOLS = [
  { key: 'channels', label: '渠道监控', hint: '熔断状态', icon: 'M12 3v4M12 17v4M5 12h4M15 12h4', badge: true },
  { key: 'settings', label: '系统设置', hint: '刮削与网络', icon: 'M12 9a3 3 0 100 6 3 3 0 000-6zM12 2v2M12 20v2M2 12h2M20 12h2' },
]

function go(key) {
  emit('navigate', key)
}
</script>

<template>
  <nav class="nav" aria-label="主导航">
    <div class="nav-brand">
      <span class="nav-brand-name">JavSP</span>
      <span class="nav-brand-sub">刮削管理台</span>
    </div>

    <div class="nav-group">
      <p class="nav-group-title">工作流</p>
      <button
        v-for="item in WORKFLOW"
        :key="item.key"
        class="nav-item"
        :class="{ 'nav-item--active': props.active === item.key }"
        :aria-current="props.active === item.key ? 'page' : undefined"
        @click="go(item.key)"
      >
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path :d="item.icon" />
        </svg>
        <span class="nav-text">
          <span class="nav-label">{{ item.label }}</span>
          <span class="nav-hint">{{ item.hint }}</span>
        </span>
      </button>

      <button
        v-for="item in DESTRUCTIVE"
        :key="item.key"
        class="nav-item nav-item--danger"
        :class="{ 'nav-item--active': props.active === item.key }"
        :aria-current="props.active === item.key ? 'page' : undefined"
        @click="go(item.key)"
      >
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path :d="item.icon" />
        </svg>
        <span class="nav-text">
          <span class="nav-label">{{ item.label }}</span>
          <span class="nav-hint">{{ item.hint }}</span>
        </span>
      </button>
    </div>

    <div class="nav-group">
      <p class="nav-group-title">工具</p>
      <button
        v-for="item in TOOLS"
        :key="item.key"
        class="nav-item"
        :class="{ 'nav-item--active': props.active === item.key }"
        :aria-current="props.active === item.key ? 'page' : undefined"
        @click="go(item.key)"
      >
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path :d="item.icon" />
        </svg>
        <span class="nav-text">
          <span class="nav-label">{{ item.label }}</span>
          <span v-if="item.badge && props.tripCount > 0" class="nav-badge">
            {{ props.tripCount }}
          </span>
          <span v-else class="nav-hint">{{ item.hint }}</span>
        </span>
      </button>
    </div>
  </nav>
</template>

<style scoped>
.nav {
  width: var(--nav-width);
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  padding: var(--space-4) var(--space-3);
  background: var(--color-bg-surface);
  border-right: 1px solid var(--color-border-light);
  overflow-y: auto;
}

.nav-brand {
  padding: var(--space-2) var(--space-3) 0;
}

.nav-brand-name {
  display: block;
  font-size: var(--font-lg);
  font-weight: 600;
  letter-spacing: -0.01em;
}

.nav-brand-sub {
  display: block;
  margin-top: 1px;
  font-size: var(--font-xs);
  color: var(--color-text-tertiary);
}

.nav-group { display: flex; flex-direction: column; gap: 2px; }

.nav-group-title {
  padding: 0 var(--space-3) var(--space-2);
  font-size: var(--font-xs);
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--color-text-tertiary);
}

.nav-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  min-height: 38px;
  padding: var(--space-1) var(--space-3);
  font-family: inherit;
  text-align: left;
  color: var(--color-text-secondary);
  background: transparent;
  border: 0;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--transition-fast), color var(--transition-fast);
}

.nav-item:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }
.nav-item:focus-visible { outline: none; box-shadow: var(--focus-ring); }

.nav-item--active {
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-weight: 500;
}

.nav-item--danger { color: var(--color-danger); }
.nav-item--danger:hover { background: var(--color-danger-soft); color: var(--color-danger); }
.nav-item--danger.nav-item--active { background: var(--color-danger-soft); color: var(--color-danger); }

.nav-icon {
  width: 17px;
  height: 17px;
  flex-shrink: 0;
}

.nav-text { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.nav-label { font-size: var(--font-base); }
.nav-hint { font-size: var(--font-xs); color: var(--color-text-tertiary); }

.nav-badge {
  align-self: flex-start;
  min-width: 17px;
  height: 17px;
  padding: 0 5px;
  font-size: var(--font-xs);
  font-weight: 600;
  line-height: 17px;
  text-align: center;
  color: var(--color-text-inverse);
  background: var(--color-danger);
  border-radius: var(--radius-full);
}

@media (max-width: 860px) {
  .nav {
    width: 100%;
    flex-direction: row;
    align-items: center;
    gap: var(--space-2);
    overflow-x: auto;
    border-right: 0;
    border-bottom: 1px solid var(--color-border-light);
  }
  .nav-brand, .nav-group-title { display: none; }
  .nav-group { flex-direction: row; gap: var(--space-1); }
  .nav-item { min-height: 32px; padding: 0 var(--space-2); }
  .nav-hint { display: none; }
  .nav-icon { width: 15px; height: 15px; }
}
</style>
