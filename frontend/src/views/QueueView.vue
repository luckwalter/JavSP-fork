<script setup>
/** 刮削队列: 批量刮削选中影片
 *  相比旧版: 进度状态常驻可见(状态栏同步); 两种模式按钮视觉明确区分;
 *  「刮削并整理」会改动文件 —— 走确认对话框, 且默认不自动触发。
 */
import { ref, computed, watch, onMounted, inject } from 'vue'
import * as api from '../api'
import { persistState, saveState } from '../composables/usePersist'

const props = defineProps({ health: { type: Object, default: () => ({}) } })
const emit = defineEmits(['navigate'])

// 确认对话框由 App.vue 通过 provide 注入 —— 必须用 inject 而非 emit:
// emit 是同步的且不返回 Promise, `await emit(...)` 恒为 undefined,
// 会导致"点了确认也没反应"。
const askConfirm = inject('javsp:confirm', null)

const guids = ref(persistState('queueGuids', []))
const running = ref(false)
const index = ref(0)
const total = ref(0)
const current = ref('')
const crawlers = ref([])
const log = ref(persistState('queueLog', []))
const done = ref(null)

watch([guids, log], () => {
  saveState('queueGuids', guids.value)
  saveState('queueLog', log.value)
}, { deep: true })

const pct = computed(() => (total.value ? Math.round((index.value / total.value) * 100) : 0))

// 从影片库带过来(同一 sessionStorage), 避免用户返回再选一遍
onMounted(() => {
  const lib = persistState('movies', [])
  if (!guids.value.length && lib.length) {
    // 无选择时不做自动全选 —— 批量刮削有外部成本, 应由用户明确发起
  }
})

async function run(withOrganize) {
  if (!guids.value.length || running.value) return

  if (withOrganize) {
    // inject 缺失时不阻断操作(仅失去确认护栏), 但要明确告知而不是静默
    const ok = askConfirm
      ? await askConfirm({
          title: '确认刮削并整理',
          impact: `将同时刮削 ${guids.value.length} 部影片并把结果写入磁盘`,
          detail: [
            '此操作会重命名文件、移动目录、生成 NFO 与封面。',
            '原始文件位置会改变, 且不会自动回滚。',
            '建议先只做「刮削」, 核对结果后再整理。',
          ],
          confirmText: '我已了解, 继续整理',
        })
      : false
    if (!ok) return
  }

  running.value = true
  index.value = 0
  total.value = guids.value.length
  current.value = ''
  crawlers.value = []
  log.value = []
  done.value = null

  try {
    await api.batchStream(
      { guids: guids.value, organize: withOrganize },
      (d) => {
        if (d.index !== undefined) index.value = d.index
        if (d.total !== undefined) total.value = d.total
        if (d.avid !== undefined) current.value = d.avid
        if (d.crawlers) crawlers.value = d.crawlers
        if (d.title !== undefined) {
          log.value = [
            ...log.value,
            { index: d.index, avid: d.avid, ok: d.ok, title: d.title, sources: d.sources },
          ]
        }
        if (d.type === 'all_done') done.value = { success: d.success, fail: d.fail, total: d.total }
      },
    )
  } catch (e) {
    log.value = [...log.value, { index: index.value, avid: current.value, ok: false, title: '失败: ' + e.message, sources: null }]
  } finally {
    running.value = false
    emit('navigate', 'review')
  }
}

function clearQueue() {
  if (running.value) return
  guids.value = []
  log.value = []
  done.value = null
  index.value = 0
  total.value = 0
}

const SOURCE_TONE = {
  ok: 'success', timeout: 'warning', network_error: 'danger', dns_error: 'danger',
  tls_error: 'danger', http_error: 'danger', blocked: 'danger', error: 'danger',
  not_found: 'info', invalid_response: 'info', duplicate: 'info',
}

function sourceRows(sources) {
  if (!sources || typeof sources !== 'object') return []
  return Object.keys(sources).map((site) => {
    const s = sources[site] || {}
    return {
      site,
      dvdid: s.dvdid || '—',
      title: s.title || '—',
      cover: !!s.has_cover,
      genre: !!s.has_genre,
      actress: !!s.has_actress,
      contributed: !!s.contributed,
      breaker: s.breaker || '',
      healthStatus: s.health_status || '',
      healthText: s.health_text || '',
    }
  })
}

function summary(sources) {
  const rows = sourceRows(sources)
  if (!rows.length) return '无数据'
  const ok = rows.filter((r) => r.contributed).length
  return `${ok}/${rows.length} 有贡献`
}
</script>

<template>
  <div>
    <div class="page-head">
      <h1 class="page-title">刮削队列</h1>
      <p class="page-desc">批量抓取元数据。已熔断的渠道会被自动跳过, 不浪费等待时间</p>
    </div>

    <div class="section">
      <div class="stat-row" style="margin-bottom: var(--space-4);">
        <div class="stat">
          <p class="stat-label">待刮削</p>
          <p class="stat-value">{{ guids.length }}</p>
        </div>
        <div class="stat">
          <p class="stat-label">进度</p>
          <p class="stat-value">{{ index }}<span class="stat-unit">/ {{ total || 0 }}</span></p>
        </div>
        <div class="stat">
          <p class="stat-label">成功</p>
          <p class="stat-value" style="color: var(--color-success);">{{ done?.success ?? 0 }}</p>
        </div>
        <div class="stat">
          <p class="stat-label">失败</p>
          <p class="stat-value" style="color: var(--color-danger);">{{ done?.fail ?? 0 }}</p>
        </div>
      </div>

      <div class="toolbar">
        <button class="btn btn--primary" :disabled="!guids.length || running" @click="run(false)">
          <span v-if="running" class="spinner" aria-hidden="true"></span>
          {{ running ? '进行中' : '仅刮削（不改动文件）' }}
        </button>
        <button class="btn btn--danger" :disabled="!guids.length || running" @click="run(true)">
          刮削并整理（改动文件）
        </button>
        <div class="toolbar-spacer"></div>
        <button class="btn" :disabled="!guids.length || running" @click="clearQueue">清空队列</button>
      </div>

      <p v-if="!guids.length" class="field-hint">
        队列为空。请到「影片库」选择要刮削的影片。
        <button class="link" @click="emit('navigate', 'library')">前往影片库 →</button>
      </p>
      <p v-else-if="health.tripped > 0" class="field-hint" style="color: var(--color-warning);">
        有 {{ health.tripped }} 个渠道已熔断, 本次刮削会自动跳过它们。
      </p>
    </div>

    <!-- 进度 -->
    <div v-if="running || index > 0" class="section">
      <h2 class="section-title">当前进度</h2>
      <div class="progress" style="margin-bottom: var(--space-2);">
        <div class="progress-bar" :style="{ width: pct + '%' }"></div>
      </div>
      <p class="field-hint">
        第 {{ index }}/{{ total }} 部 · {{ current || '处理中' }}
      </p>
      <div v-if="crawlers.length" class="crawler-chips">
        <span v-for="c in crawlers" :key="c.crawler" class="pill"
              :class="`pill--${SOURCE_TONE[c.status] || 'muted'}`">
          {{ c.crawler }}
        </span>
      </div>
    </div>

    <!-- 结果 -->
    <div v-if="log.length" class="section">
      <div class="toolbar">
        <h2 class="section-title" style="margin: 0;">刮削结果</h2>
        <div class="toolbar-spacer"></div>
        <button class="btn btn--sm" @click="emit('navigate', 'review')">前往审阅 →</button>
      </div>
      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th style="width: 46px;">#</th>
              <th style="width: 130px;">番号</th>
              <th style="width: 78px;">结果</th>
              <th style="width: 110px;">数据源</th>
              <th>标题</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in log" :key="r.index">
              <td class="dim">{{ r.index }}</td>
              <td><code>{{ r.avid }}</code></td>
              <td>
                <span class="pill" :class="r.ok ? 'pill--success' : 'pill--danger'">
                  {{ r.ok ? '成功' : '失败' }}
                </span>
              </td>
              <td><span class="pill pill--muted">{{ summary(r.sources) }}</span></td>
              <td class="title-cell">{{ r.title || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.crawler-chips { display: flex; flex-wrap: wrap; gap: var(--space-1); margin-top: var(--space-3); }
.title-cell { color: var(--color-text-secondary); }
.dim { color: var(--color-text-tertiary); }
.link {
  padding: 0;
  font-family: inherit;
  font-size: var(--font-sm);
  color: var(--color-primary);
  background: none;
  border: 0;
  cursor: pointer;
}
.link:hover { text-decoration: underline; }
code { font-family: var(--font-mono); font-size: var(--font-sm); }
</style>
