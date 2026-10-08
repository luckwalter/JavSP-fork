<script setup>
/** 渠道监控: 各渠道健康与熔断状态
 *  数据源: GET /api/channels(只读, 读缓存), POST /api/channels/check(主动探活)
 *  说明: 平时无需手动点 —— 后台每5 分钟自动探活; 「立即探活」用于刚改完配置想立刻确认。
 */
import { ref, onMounted, computed } from 'vue'
import * as api from '../api'

const emit = defineEmits(['refresh-health'])

const rows = ref([])
const summary = ref({})
const interval = ref(0)
const threshold = ref(0)
const loading = ref(false)
const probing = ref(false)
const lastChecked = ref('')

const STATUS_TYPE = {
  ok: 'success', timeout: 'warning', dns_error: 'danger', tls_error: 'danger',
  network_error: 'danger', http_error: 'danger', blocked: 'danger',
  credential_error: 'danger', error: 'danger', invalid_response: 'warning',
  not_found: 'info', duplicate: 'info', code_mismatch: 'info',
  outage: 'warning', unchecked: 'info', unknown: 'info',
}

// 视图筛选: all=全部 / active=仅启用 / inactive=仅未启用
// v0.2.2 修正: 原实现把「启用中」与「未启用」拆成两张表, 用户以为渠道没显示全
// (实测接口返回 21 个, 与 CrawlerID 枚举完全一致, 是呈现方式造成误解)。
// 现改为**单表 + 筛选**, 默认展示全部, 保证"所有渠道"一眼可见。
const view = ref('all')

const visibleRows = computed(() => {
  if (view.value === 'active') return rows.value.filter((r) => r.active)
  if (view.value === 'inactive') return rows.value.filter((r) => !r.active)
  return rows.value
})

const activeCount = computed(() => rows.value.filter((r) => r.active).length)
const inactiveCount = computed(() => rows.value.filter((r) => !r.active).length)

function statusType(row) {
  if (row.breaker === 'open') return 'danger'
  return STATUS_TYPE[row.status] || 'info'
}

async function load(probe = false) {
  if (probe) probing.value = true
  else loading.value = true
  try {
    if (probe) {
      await api.checkChannels()
      const d = await api.getChannels()
      rows.value = d.sources || []
      summary.value = d.summary || {}
      lastChecked.value = new Date().toLocaleTimeString()
    } else {
      const d = await api.getChannels()
      rows.value = d.sources || []
      summary.value = d.summary || {}
      interval.value = d.probe_interval || 0
      threshold.value = d.threshold || 0
      const latest = rows.value.reduce((m, r) => Math.max(m, r.checked_at || 0), 0)
      if (latest) lastChecked.value = new Date(latest * 1000).toLocaleTimeString()
    }
    emit('refresh-health')
  } catch (e) {
    console.warn('渠道监控加载失败', e)
  } finally {
    loading.value = false
    probing.value = false
  }
}

onMounted(() => load(false))
</script>

<template>
  <div>
    <div class="page-head">
      <h1 class="page-title">渠道监控</h1>
      <p class="page-desc">
        各刮削渠道的可用性。连续失败 2 次会<b>自动熔断</b>并跳过, 到期自动试探恢复
      </p>
    </div>

    <!-- 概览 -->
    <div class="stat-row" style="margin-bottom: var(--space-4);">
      <div class="stat">
        <p class="stat-label">启用渠道</p>
        <p class="stat-value">{{ summary.active ?? '—' }}</p>
      </div>
      <div class="stat">
        <p class="stat-label">正常</p>
        <p class="stat-value" style="color: var(--color-success);">{{ summary.ok ?? 0 }}</p>
      </div>
      <div class="stat">
        <p class="stat-label">已熔断跳过</p>
        <p class="stat-value" style="color: var(--color-danger);">{{ summary.tripped ?? 0 }}</p>
      </div>
      <div class="stat">
        <p class="stat-label">待检查</p>
        <p class="stat-value" style="color: var(--color-text-tertiary);">{{ summary.unchecked ?? 0 }}</p>
      </div>
    </div>

    <div v-if="summary.tripped > 0" class="notice notice--warning" style="margin-bottom: var(--space-4);">
      有 {{ summary.tripped }} 个渠道已被熔断, 刮削时会直接跳过(省下重试等待)。
      若刚改完代理或镜像配置, 可点「立即探活」重测。
    </div>

    <div class="toolbar">
      <button class="btn" :disabled="loading" @click="load(false)">刷新</button>
      <button class="btn btn--primary" :disabled="probing" @click="load(true)">
        <span v-if="probing" class="spinner" aria-hidden="true"></span>
        {{ probing ? '探活中' : '立即探活' }}
      </button>
      <span v-if="lastChecked" class="pill pill--muted">上次探活 {{ lastChecked }}</span>
      <span v-if="interval" class="pill pill--info">
        后台每 {{ Math.round(interval / 60) }} 分钟自动探活
      </span>
      <span v-if="threshold" class="pill pill--info">连续失败 {{ threshold }} 次熔断</span>
    </div>

    <!-- 全部渠道(单表+筛选, 保证"所有渠道"一眼可见) -->
    <div v-if="rows.length" class="section">
      <div class="toolbar" style="margin-bottom: var(--space-3);">
        <div class="segmented">
          <button class="seg" :class="{ 'seg--on': view === 'all' }" @click="view = 'all'">
            全部 {{ rows.length }}
          </button>
          <button class="seg" :class="{ 'seg--on': view === 'active' }" @click="view = 'active'">
            启用 {{ activeCount }}
          </button>
          <button class="seg" :class="{ 'seg--on': view === 'inactive' }" @click="view = 'inactive'">
            未启用 {{ inactiveCount }}
          </button>
        </div>
        <span class="field-hint" style="margin: 0;">
          未启用的渠道可在「系统设置 · 刮削源」中勾选启用
        </span>
      </div>

      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th style="width: 118px;">渠道</th>
              <th style="width: 130px;">状态</th>
              <th style="width: 100px;">刮削参与</th>
              <th style="width: 80px;">耗时</th>
              <th style="width: 110px;">近期命中率</th>
              <th style="width: 150px;">站点</th>
              <th>失败原因</th>
              <th style="width: 70px;">冷却</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in visibleRows" :key="r.source" :class="{ 'row-off': !r.active }">
              <td>
                <code>{{ r.source }}</code>
                <span v-if="!r.active" class="pill pill--muted" style="margin-left: 4px;">未启用</span>
              </td>
              <td><span class="pill" :class="`pill--${statusType(r)}`">{{ r.status_text }}</span></td>
              <td>
                <span v-if="!r.active" class="dim">-</span>
                <span v-else-if="r.breaker === 'open'" class="pill pill--danger">已跳过</span>
                <span v-else-if="r.breaker === 'half_open'" class="pill pill--warning">试探中</span>
                <template v-else>
                  <span class="pill" :class="r.consecutive_failures > 0 ? 'pill--warning' : 'pill--success'">
                    参与
                  </span>
                  <!-- 已经失败过但还没到熔断阈值时, 光写"参与"会让人以为一切正常,
                       与左边"内部错误/HTTP 错误"的状态自相矛盾。把 x/2 摆出来,
                       用户才知道"再失败一次就会被跳过"。 -->
                  <span v-if="r.consecutive_failures > 0" class="dim" style="margin-left: 4px;">
                    失败 {{ r.consecutive_failures }}/{{ threshold }}
                  </span>
                </template>
              </td>
              <td class="num">{{ r.elapsed_ms ? r.elapsed_ms + ' ms' : '—' }}</td>
              <td class="num">
                <template v-if="r.hit_rate !== null">
                  {{ Math.round(r.hit_rate) }}%
                  <span class="dim">({{ r.scrape_success }}/{{ r.scrape_samples }})</span>
                </template>
                <span v-else class="dim">暂无样本</span>
              </td>
              <td class="dim">{{ r.domain || '—' }}</td>
              <td class="reason">{{ r.reason || '—' }}</td>
              <td class="num">{{ r.cooldown_remaining > 0 ? r.cooldown_remaining + 's' : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>


    <div v-if="!rows.length && !loading" class="empty">
      <p class="empty-title">暂无渠道数据</p>
      <p>点「刷新」重新加载</p>
    </div>
  </div>
</template>

<style scoped>
.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.dim { color: var(--color-text-tertiary); font-size: var(--font-sm); }

/* 未启用的行整体淡化 —— 与"启用中"一眼区分, 但仍完整可见 */
.row-off td { opacity: 0.62; }
.row-off:hover td { opacity: 1; }

/* 视图筛选器 */
.segmented {
  display: inline-flex;
  border: 1px solid var(--color-border-medium);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.seg {
  padding: var(--space-1) var(--space-3);
  font-family: inherit;
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
  background: var(--color-bg-surface);
  border: 0;
  border-right: 1px solid var(--color-border-light);
  cursor: pointer;
}

.seg:last-child { border-right: 0; }
.seg:hover { background: var(--color-bg-hover); }
.seg--on { background: var(--color-primary-soft); color: var(--color-primary); font-weight: 500; }
.seg:focus-visible { outline: none; box-shadow: var(--focus-ring); }

.reason {
  color: var(--color-danger);
  font-size: var(--font-sm);
  word-break: break-word;
}
code {
  font-family: var(--font-mono);
  font-size: var(--font-sm);
}
</style>
