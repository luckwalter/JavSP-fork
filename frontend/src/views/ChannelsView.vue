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

// --------------------- 渠道 Cookie 配置弹窗 ---------------------
const cookieDialog = ref({ open: false, source: '', text: '', loading: false, count: null })
const flash = ref('')

function showFlash(msg) {
  flash.value = msg
  setTimeout(() => { flash.value = '' }, 4000)
}

async function openCookieDialog(r) {
  if (!r.cookie_supported) return
  cookieDialog.value = { open: true, source: r.source, text: '', loading: false, count: null }
  try {
    const d = await api.getChannelCookie(r.source)
    cookieDialog.value.text = (d.cookie && d.cookie.length)
      ? JSON.stringify(d.cookie, null, 2)
      : ''
    cookieDialog.value.count = d.count
  } catch (e) {
    console.warn('读取 cookie 失败', e)
  }
}

async function saveCookie() {
  cookieDialog.value.loading = true
  try {
    const d = await api.putChannelCookie(cookieDialog.value.source, cookieDialog.value.text)
    cookieDialog.value.count = d.count
    cookieDialog.value.open = false
    showFlash(d.note)
  } catch (e) {
    console.warn('保存 cookie 失败', e)
    alert('保存失败：' + (e.message || e))
  } finally {
    cookieDialog.value.loading = false
  }
}

async function clearCookie() {
  cookieDialog.value.loading = true
  try {
    const d = await api.putChannelCookie(cookieDialog.value.source, '[]')
    cookieDialog.value.count = 0
    cookieDialog.value.text = ''
    cookieDialog.value.open = false
    showFlash(d.note)
  } catch (e) {
    console.warn('清空 cookie 失败', e)
    alert('清空失败：' + (e.message || e))
  } finally {
    cookieDialog.value.loading = false
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

    <div v-if="flash" class="notice notice--success" style="margin-bottom: var(--space-4);">
      {{ flash }}
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
                <button
                  v-if="r.cookie_supported"
                  class="btn btn--xs"
                  title="配置该渠道的浏览器 Cookie"
                  @click="openCookieDialog(r)"
                >配置Cookie</button>
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

    <!-- 渠道 Cookie 配置弹窗 -->
    <div v-if="cookieDialog.open" class="modal-mask" @click.self="cookieDialog.open = false">
      <div class="modal">
        <h3 class="modal-title">配置 {{ cookieDialog.source }} 的 Cookie</h3>
        <p class="modal-desc">粘贴从浏览器扩展 Cookie-Editor 导出的 JSON（数组格式），保存后立即生效，无需重启。</p>
        <textarea
          v-model="cookieDialog.text"
          class="cookie-text"
          spellcheck="false"
          placeholder='[{"name":"PHPSESSID","value":"..."},{"name":"existmag","value":"mag"}]'
        ></textarea>

        <div class="cookie-guide">
          <p class="guide-title">如何获取（浏览器扩展 Cookie-Editor）</p>
          <ol>
            <li>用能正常打开该站点的浏览器，打开站点任一页面。</li>
            <li>安装扩展
              <a href="https://chrome.google.com/webstore/detail/cookie-editor/" target="_blank" rel="noopener">Cookie-Editor</a>
              （Chrome / Edge / Firefox 应用店均有）。
            </li>
            <li>点扩展图标，确认域名是该站点，点 <b>Export</b> → 选 <b>JSON</b> → 复制整段。</li>
            <li>粘贴到上方文本框，点「保存」即可。</li>
          </ol>
        </div>

        <p v-if="cookieDialog.count !== null" class="modal-hint">当前已配置 {{ cookieDialog.count }} 条 cookie</p>

        <div class="modal-actions">
          <button class="btn" :disabled="cookieDialog.loading" @click="clearCookie">清空</button>
          <span class="spacer"></span>
          <button class="btn" @click="cookieDialog.open = false">取消</button>
          <button class="btn btn--primary" :disabled="cookieDialog.loading" @click="saveCookie">
            {{ cookieDialog.loading ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
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

/* 渠道列「配置 Cookie」小按钮 */
.btn--xs {
  margin-left: 6px;
  padding: 1px 8px;
  font-size: var(--font-xs);
  line-height: 1.6;
  border-radius: var(--radius-sm);
}

/* Cookie 配置弹窗 */
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
  padding: var(--space-4);
}
.modal {
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-medium);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg, 0 12px 32px rgba(0, 0, 0, 0.25));
  width: min(560px, 100%);
  max-height: 88vh;
  overflow-y: auto;
  padding: var(--space-5);
}
.modal-title { font-size: var(--font-lg); margin: 0 0 var(--space-2); }
.modal-desc { color: var(--color-text-secondary); font-size: var(--font-sm); margin: 0 0 var(--space-3); }
.cookie-text {
  width: 100%;
  min-height: 140px;
  resize: vertical;
  font-family: var(--font-mono);
  font-size: var(--font-sm);
  line-height: 1.5;
  padding: var(--space-2);
  border: 1px solid var(--color-border-medium);
  border-radius: var(--radius-md);
  background: var(--color-bg-base);
  color: var(--color-text-primary);
  box-sizing: border-box;
}
.cookie-text:focus { outline: none; box-shadow: var(--focus-ring); border-color: var(--color-primary); }
.cookie-guide {
  margin-top: var(--space-3);
  padding: var(--space-3);
  background: var(--color-bg-base);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}
.cookie-guide .guide-title { font-weight: 600; margin: 0 0 var(--space-1); color: var(--color-text-primary); }
.cookie-guide ol { margin: 0; padding-left: 1.2em; }
.cookie-guide li { margin-bottom: 2px; }
.cookie-guide a { color: var(--color-primary); }
.modal-hint { font-size: var(--font-sm); color: var(--color-text-tertiary); margin: var(--space-3) 0 0; }
.modal-actions { display: flex; align-items: center; gap: var(--space-2); margin-top: var(--space-4); }
.modal-actions .spacer { flex: 1; }
</style>
