<script setup>
/** 破坏性操作二次确认
 *  为什么必须独立于普通对话框: 「刮削并整理」会真实改名+移动文件, 不可撤销。
 *  设计上做到三重防护:
 *    1. 视觉隔离 —— 危险色, 与安全操作绝不并排
 *    2. 明示后果 —— 直接写出将发生什么(改N 个目录/文件)
 *    3. 主动确认 —— 默认焦点在「取消」, 回车不会误触
 */
import { ref, watch, nextTick } from 'vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  title: { type: String, default: '确认执行' },
  impact: { type: String, default: '' },
  detail: { type: [String, Array], default: '' },
  confirmText: { type: String, default: '确认' },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['confirm', 'cancel'])

const cancelRef = ref(null)

// 打开时把焦点放到「取消」上 —— 避免用户习惯性回车直接执行危险操作
watch(() => props.show, async (v) => {
  if (v) {
    await nextTick()
    cancelRef.value?.focus()
  }
})

function onKey(e) {
  if (e.key === 'Escape' && !props.busy) emit('cancel')
}
</script>

<template>
  <div v-if="show" class="overlay" @click.self="!busy && emit('cancel')" @keydown="onKey">
    <div class="dialog" role="alertdialog" aria-modal="true" :aria-label="title">
      <div class="dialog-icon" aria-hidden="true">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 9v5M12 17h.01" />
          <path d="M10.3 3.9L2.4 17.5A1.9 1.9 0 0 0 4 20.3h16a1.9 1.9 0 0 0 1.6-2.8L13.7 3.9a1.9 1.9 0 0 0-3.4 0z" />
        </svg>
      </div>

      <h2 class="dialog-title">{{ title }}</h2>
      <p v-if="impact" class="dialog-impact">{{ impact }}</p>

      <div v-if="detail && String(detail).length" class="dialog-detail">
        <p v-for="(line, i) in (Array.isArray(detail) ? detail : [detail])" :key="i">{{ line }}</p>
      </div>

      <div class="dialog-actions">
        <button ref="cancelRef" class="btn" :disabled="busy" @click="emit('cancel')">
          取消
        </button>
        <button class="btn btn--danger" :disabled="busy" @click="emit('confirm')">
          <span v-if="busy" class="spinner" aria-hidden="true"></span>
          {{ busy ? '执行中' : confirmText }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-overlay);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-6);
  background: rgba(31, 35, 41, 0.45);
}

.dialog {
  width: 100%;
  max-width: 400px;
  padding: var(--space-6);
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
}

.dialog-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  margin-bottom: var(--space-3);
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-radius: var(--radius-md);
}

.dialog-title {
  font-size: var(--font-lg);
  font-weight: 600;
  margin-bottom: var(--space-2);
}

.dialog-impact {
  font-size: var(--font-base);
  color: var(--color-text-primary);
  margin-bottom: var(--space-3);
}

.dialog-detail {
  padding: var(--space-3);
  margin-bottom: var(--space-5);
  font-size: var(--font-sm);
  line-height: 1.7;
  color: var(--color-text-secondary);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
}

.dialog-detail p { margin: 0; }

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
</style>
