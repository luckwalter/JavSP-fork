<script setup>
/** 刮削源: 选源管理 + 渠道实时状态合一(原渠道监控页升级)
 *  数据源: GET /api/channels(状态, 只读缓存) + GET /api/config(选源清单)
 *          保存走 PUT /api/config(与设置页同一通道, 后端锁内合并)
 *  选源模型: 后端 CrawlerSelect 是五维清单(normal/fc2/cid/getchu/gyutto),
 *            刮削实际只消费 normal/fc2/cid 三类(core.py 按 movie.data_src 取)。
 *            行内两类控件, 语义分层:
 *              - 总开关: 开 = 该源参与刮削(加入它支持的全部类别), 关 = 三类清单全移除;
 *              - 类别 chip: 细粒度, 点亮 = 该源进入这一类番号的清单。
 *            getchu/gyutto 两维的配置原样保留, 页面不渲染(避免误导)。
 *  未保存机制: chip 改动进 draft, 「保存并应用」才落盘; 离开页面有确认(见 guard)。
 */
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick, inject } from 'vue'
import * as api from '../api'
import { KINDS, KIND_LABEL, SOURCE_KINDS, CAPABLE_SOURCES, SOURCE_GROUPS, SITE_LABELS, PRESETS } from '../utils/sourceMeta'

const emit = defineEmits(['refresh-health'])
const askConfirm = inject('javsp:confirm')
const registerLeaveGuard = inject('javsp:leave-guard', () => {})

const rows = ref([])
const summary = ref({})
const interval = ref(0)
const threshold = ref(0)
const loading = ref(false)
const probing = ref(false)
const lastChecked = ref('')

// 选源配置: cfg 为完整配置(保存时全量回传), draft 为编辑中的三类清单,
// savedSelect 为上次保存的基准(脏值比较用)。
const cfg = ref(null)
const savedSelect = ref({ normal: [], fc2: [], cid: [] })
const draft = ref({ normal: [], fc2: [], cid: [] })
const saving = ref(false)
const savedMsg = ref('')
let savedTimer = 0

// 状态配色: 不可用类一律 danger(红), 与「被反爬拦截」同色。
// 缺登记会回落到 info(蓝), 让人以为状态还不严重(v0.2.12 首次加这些状态时踩过)。
const STATUS_TYPE = {
  ok: 'success', timeout: 'warning', dns_error: 'danger', tls_error: 'danger',
  network_error: 'danger', http_error: 'danger', blocked: 'danger',
  credential_error: 'danger', error: 'danger', invalid_response: 'warning',
  not_found: 'info', duplicate: 'info', code_mismatch: 'info',
  outage: 'warning', unchecked: 'info', unknown: 'info',
  // 不可用(源本身用不了, 刮削会被跳过) —— 与 blocked 同为红, 统一表示"别用它"
  ip_banned: 'danger', challenge_required: 'danger',
  client_rendered: 'danger', source_defunct: 'danger',
}
// 「有异常」口径: 不可用 / 已熔断 / 探测类故障。ok、not_found(没收录但源是好的)、
// duplicate、code_mismatch、outage(源没坏, 只是临时不可用且不熔断)不算异常。
const ABNORMAL = new Set([
  'timeout', 'dns_error', 'tls_error', 'network_error', 'http_error', 'blocked',
  'credential_error', 'error', 'invalid_response', 'ip_banned', 'challenge_required',
  'client_rendered', 'source_defunct',
])

// 视图筛选: all=全部 / abnormal=有异常 / off=未启用
const view = ref('all')

const dirty = computed(() => {
  for (const k of KINDS) {
    const a = [...draft.value[k]].sort()
    const b = [...savedSelect.value[k]].sort()
    if (a.length !== b.length || a.some((v, i) => v !== b[i])) return true
  }
  return false
})

// 页面内所有启用/未启用计数都以 draft 为口径, 保证与 chip 亮灭自洽。
// 启用口径锚定"能力集": 只有在源支持的维度里被勾选才算启用。
// 否则 dl_getchu/gyutto 等被错加进 normal 时会被算成"启用"却又"不参与", 自相矛盾。
const draftEnabled = computed(() => {
  const s = new Set()
  for (const k of KINDS) {
    const cap = SOURCE_KINDS[k] || []
    for (const v of draft.value[k]) {
      if (cap.includes(v)) s.add(v)   // 只在该维度确实是它支持的时才计入
    }
  }
  return s
})

function statusType(row) {
  if (row.breaker === 'open') return 'danger'
  return STATUS_TYPE[row.status] || 'info'
}

function isAbnormal(row) {
  return !row.usable || row.breaker === 'open' || ABNORMAL.has(row.status)
}

/** 某源应渲染的类别 chip: 能力集 ∪ 当前已勾选(用户手动加进配置的类别不丢) */
function kindsFor(source) {
  const kinds = KINDS.filter((k) => SOURCE_KINDS[k].includes(source))
  for (const k of KINDS) {
    if (draft.value[k].includes(source) && !kinds.includes(k)) kinds.push(k)
  }
  return kinds
}

/** 该源支持的能力类别(开总开关时加入的默认集合; 与 kindsFor 的区别是不含历史配置) */
function capableKinds(source) {
  return KINDS.filter((k) => SOURCE_KINDS[k].includes(source))
}

/** 访问地址 tooltip: 实测与标注值不符时提示可能改过 base_url / 走了镜像 */
function addrTitle(h) {
  return h.domain_mismatch
    ? '显示值可能与实际请求地址不符（可能改过 base_url / 走了镜像 / 域名迁移未同步）'
    : (h.domain_actual || h.domain || '')
}

/** 总开关: 关 = 三类清单全部移除(该源不参与任何刮削);
 *  开 = 加入它支持的全部类别(kindsFor 为空的源无开关, 见模板 disabled) */
function toggleSource(source) {
  if (draftEnabled.value.has(source)) {
    for (const k of KINDS) {
      const arr = draft.value[k]
      const i = arr.indexOf(source)
      if (i >= 0) arr.splice(i, 1)
    }
  } else {
    for (const k of capableKinds(source)) {
      if (!draft.value[k].includes(source)) draft.value[k].push(source)
    }
  }
}

/** 全部已注册源按分组展开(一行一源), 与 /api/channels 的状态行对齐 */
const groupedRows = computed(() => {
  const bySource = new Map(rows.value.map((r) => [r.source, r]))
  const placed = new Set()
  const groups = []
  for (const g of SOURCE_GROUPS) {
    const list = []
    for (const s of g.sources) {
      placed.add(s)
      const r = bySource.get(s)
      list.push({
        source: s,
        label: SITE_LABELS[s] || '',
        kinds: kindsFor(s),
        health: r || null,
      })
    }
    groups.push({ ...g, rows: list })
  }
  // 兜底: CrawlerID 未来新增但未在 SOURCE_GROUPS 声明的源, 不丢展示
  const rest = rows.value.filter((r) => !placed.has(r.source)).map((r) => ({
    source: r.source, label: '', kinds: kindsFor(r.source), health: r,
  }))
  if (rest.length) groups.push({ key: 'other', label: '其他', hint: '', rows: rest })
  return groups
})

const abnormalCount = computed(() => rows.value.filter(isAbnormal).length)
const offCount = computed(() =>
  rows.value.filter((r) => CAPABLE_SOURCES.includes(r.source) && !draftEnabled.value.has(r.source)).length)

/** 过滤后的分组(保持组结构; 组内无可见行时整组隐藏) */
const visibleGroups = computed(() => {
  let gi = 0
  return groupedRows.value
    .map((g) => ({
      ...g,
      rows: g.rows
        .filter((r) => {
          if (view.value === 'abnormal') return r.health && isAbnormal(r.health)
          if (view.value === 'off') return !draftEnabled.value.has(r.source)
          return true
        })
        // 行内控件序列: 总开关(kinds 非空才有)排最前, 其后是类别 chip
        .map((r) => ({
          ...r,
          controls: [
            ...(r.kinds.length ? [{ type: 'switch', source: r.source, gi: gi++ }] : []),
            ...r.kinds.map((k) => ({ type: 'kind', kind: k, source: r.source, gi: gi++ })),
          ],
        })),
    }))
    .filter((g) => g.rows.length)
})

// 键盘导航(roving tabindex): 全部可见控件(总开关 + 类别 chip)拉平为一维,
// 焦点位置 focusIdx, 只有它对应的控件 tabindex=0, 其余 -1 —— 整页只占一个
// Tab 位, 方向键在控件间/行间移动(屏幕阅读器与键盘用户的可用性兜底)。
const flatControls = computed(() => visibleGroups.value.flatMap((g) => g.rows.flatMap((r) => r.controls)))
const flatRows = computed(() => visibleGroups.value.flatMap((g) => g.rows))
const focusIdx = ref(0)

watch(focusIdx, async (i) => {
  await nextTick()
  document.querySelector(`[data-ctrl-idx="${i}"]`)?.focus()
})
watch(flatControls, (n) => {
  if (focusIdx.value >= n.length) focusIdx.value = Math.max(0, n.length - 1)
})

function onControlKeydown(e, gi) {
  const cur = flatControls.value[gi]
  if (!cur) return
  const dir = { ArrowRight: 1, ArrowLeft: -1 }[e.key]
  if (dir) {
    e.preventDefault()
    focusIdx.value = Math.max(0, Math.min(flatControls.value.length - 1, gi + dir))
    return
  }
  const vdir = { ArrowDown: 1, ArrowUp: -1 }[e.key]
  if (vdir) {
    e.preventDefault()
    const rowsArr = flatRows.value
    const ri = rowsArr.findIndex((r) => r.source === cur.source)
    const target = rowsArr[ri + vdir]
    if (target && target.controls.length) focusIdx.value = target.controls[0].gi
  }
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
      const [d, c] = await Promise.all([api.getChannels(), api.getConfig()])
      rows.value = d.sources || []
      summary.value = d.summary || {}
      interval.value = d.probe_interval || 0
      threshold.value = d.threshold || 0
      cfg.value = c
      const sel = c?.crawler?.selection || {}
      const snap = {}
      for (const k of KINDS) {
        snap[k] = (Array.isArray(sel[k]) ? sel[k] : [])
          .map((x) => (typeof x === 'string' ? x : x?.value))
          .filter(Boolean)
      }
      savedSelect.value = snap
      draft.value = { normal: [...snap.normal], fc2: [...snap.fc2], cid: [...snap.cid] }
      const latest = rows.value.reduce((m, r) => Math.max(m, r.checked_at || 0), 0)
      if (latest) lastChecked.value = new Date(latest * 1000).toLocaleTimeString()
    }
    emit('refresh-health')
  } catch (e) {
    console.warn('刮削源页加载失败', e)
  } finally {
    loading.value = false
    probing.value = false
  }
}

function toggleKind(source, kind) {
  const arr = draft.value[kind]
  const i = arr.indexOf(source)
  if (i >= 0) arr.splice(i, 1)
  else arr.push(source)
}

async function applyPreset(key) {
  if (key === 'all-on') {
    draft.value = {
      normal: [...SOURCE_KINDS.normal],
      fc2: [...SOURCE_KINDS.fc2],
      cid: [...SOURCE_KINDS.cid],
    }
    return
  }
  if (key === 'all-off') {
    const ok = await askConfirm({
      title: '全部停用刮削源',
      impact: '三类番号的选源清单都将清空',
      detail: '保存并应用后, 刮削不会从任何源获取数据(已整理的影片不受影响, 但刮不到新元数据)。之后可随时重新启用。',
      confirmText: '全部停用',
    })
    if (!ok) return
    draft.value = { normal: [], fc2: [], cid: [] }
    return
  }
  const p = PRESETS[key]
  if (p) draft.value = { normal: [...p.select.normal], fc2: [...p.select.fc2], cid: [...p.select.cid] }
}

async function save() {
  if (!cfg.value || saving.value) return
  saving.value = true
  try {
    // 全量回传: getchu/gyutto 两维不在 draft 中, 原样合并保留, 不丢用户的旧配置
    const next = {
      ...cfg.value,
      crawler: {
        ...cfg.value.crawler,
        selection: { ...cfg.value.crawler.selection, ...draft.value },
      },
    }
    const r = await api.putConfig(next)
    cfg.value = next
    await load(false)   // 重拉: savedSelect 基准与渠道 active 状态同步刷新
    emit('refresh-health')
    savedMsg.value = `选源已保存并生效(改动 ${r.changed ?? 0} 个字段)`
    window.clearTimeout(savedTimer)
    savedTimer = window.setTimeout(() => { savedMsg.value = '' }, 5000)
  } catch (e) {
    window.alert(`保存失败: ${e.message}`)
  } finally {
    saving.value = false
  }
}

function discard() {
  draft.value = {
    normal: [...savedSelect.value.normal],
    fc2: [...savedSelect.value.fc2],
    cid: [...savedSelect.value.cid],
  }
}

// 离开守卫: 有未保存更改时先问过用户。resolve(true)=放弃更改离开 / false=留下。
async function guard() {
  if (!dirty.value) return true
  return askConfirm({
    title: '离开刮削源页',
    impact: '有未保存的选源更改',
    detail: '离开将放弃这些更改; 也可以先点「保存并应用」再离开。',
    confirmText: '放弃更改并离开',
  })
}

onMounted(() => {
  load(false)
  registerLeaveGuard(guard)
})
onBeforeUnmount(() => {
  registerLeaveGuard(null)
  window.clearTimeout(savedTimer)
})
</script>

<template>
  <div>
    <div class="page-head">
      <h1 class="page-title">刮削源</h1>
      <p class="page-desc">
        总开关控制某个源是否参与刮削; 点亮类别 chip 可精细控制它参与哪些番号类型(一部影片会合并多个源的数据)。
        连续失败 2 次会<b>自动熔断</b>并跳过, 到期自动试探恢复
      </p>
    </div>

    <!-- 概览: 启用数以页面 chip 口径自洽, 状态数取探活结论 -->
    <div class="stat-row" style="margin-bottom: var(--space-4);">
      <div class="stat">
        <p class="stat-label">启用源</p>
        <p class="stat-value">{{ draftEnabled.size }}</p>
      </div>
      <div class="stat">
        <p class="stat-label">正常</p>
        <p class="stat-value" style="color: var(--color-success);">
          {{ rows.filter((r) => r.status === 'ok').length }}
        </p>
      </div>
      <div class="stat">
        <p class="stat-label">已熔断跳过</p>
        <p class="stat-value" style="color: var(--color-danger);">
          {{ rows.filter((r) => r.breaker === 'open').length }}
        </p>
      </div>
      <div class="stat">
        <p class="stat-label" title="配置里已勾选, 但源本身当前用不了, 刮削会跳过">不可用(跳过)</p>
        <p class="stat-value" style="color: var(--color-warning);">
          {{ rows.filter((r) => draftEnabled.has(r.source) && !r.usable).length }}
        </p>
      </div>
    </div>

    <div v-if="rows.some((r) => draftEnabled.has(r.source) && !r.usable)"
         class="notice notice--warning" style="margin-bottom: var(--space-4);">
      有 {{ rows.filter((r) => draftEnabled.has(r.source) && !r.usable).length }} 个已启用源<b>不会参与刮削</b>
      （{{ rows.filter((r) => draftEnabled.has(r.source) && !r.usable).map((r) => r.source).join('、') }}）。
      它们不是"没收录"，而是源本身当前用不了（被封 / 需人机验证 / 已改前端渲染 / 站点已失效），
      已从刮削流程中跳过以免浪费时间。
    </div>

    <div v-if="rows.some((r) => r.breaker === 'open')" class="notice notice--warning"
         style="margin-bottom: var(--space-4);">
      有 {{ rows.filter((r) => r.breaker === 'open').length }} 个渠道已被熔断, 刮削时会直接跳过(省下重试等待)。
      若刚改完代理或镜像配置, 可点「立即探活」重测。
    </div>

    <!-- 未保存操作栏: 保存并应用 / 放弃更改(=撤销, 恢复上次保存的配置) -->
    <div v-if="dirty" class="save-bar">
      <span class="save-bar-text">有未保存的选源更改</span>
      <button class="btn btn--sm btn--primary" :disabled="saving" @click="save">
        <span v-if="saving" class="spinner" aria-hidden="true"></span>
        {{ saving ? '保存中' : '保存并应用' }}
      </button>
      <button class="btn btn--sm" :disabled="saving" @click="discard">放弃更改</button>
    </div>
    <div v-else-if="savedMsg" class="notice notice--success" style="margin-bottom: var(--space-3);">
      {{ savedMsg }}
    </div>

    <div class="toolbar">
      <button class="btn" :disabled="loading" @click="load(false)">刷新</button>
      <button class="btn btn--primary" :disabled="probing" @click="load(true)">
        <span v-if="probing" class="spinner" aria-hidden="true"></span>
        {{ probing ? '探活中' : '立即探活' }}
      </button>
      <span v-if="lastChecked" class="pill pill--muted">上次探活 {{ lastChecked }}</span>
      <span v-if="interval" class="pill pill--info">后台每 {{ Math.round(interval / 60) }} 分钟自动探活</span>
      <span v-if="threshold" class="pill pill--info">连续失败 {{ threshold }} 次熔断</span>
    </div>

    <!-- 预设 + 过滤 -->
    <div class="toolbar" style="margin-bottom: var(--space-4);">
      <div class="preset-group" role="group" aria-label="选源预设">
        <span class="toolbar-label">预设</span>
        <button v-for="p in ['recommend', 'core']" :key="p" class="btn btn--sm"
                :title="PRESETS[p].desc" @click="applyPreset(p)">{{ PRESETS[p].label }}</button>
        <button class="btn btn--sm" @click="applyPreset('all-on')">全部启用</button>
        <button class="btn btn--sm btn--danger-ghost" @click="applyPreset('all-off')">全部停用</button>
      </div>
      <div class="toolbar-spacer"></div>
      <div class="segmented">
        <button class="seg" :class="{ 'seg--on': view === 'all' }" @click="view = 'all'">
          全部 {{ rows.length }}
        </button>
        <button class="seg" :class="{ 'seg--on': view === 'abnormal' }" @click="view = 'abnormal'">
          有异常 {{ abnormalCount }}
        </button>
        <button class="seg" :class="{ 'seg--on': view === 'off' }" @click="view = 'off'">
          未启用 {{ offCount }}
        </button>
      </div>
    </div>

    <!-- 分组行列表 -->
    <div v-if="rows.length">
      <section v-for="g in visibleGroups" :key="g.key" class="section">
        <div class="group-head">
          <h2 class="group-title">{{ g.label }}</h2>
          <span v-if="g.hint" class="group-hint">{{ g.hint }}</span>
        </div>
        <div class="src-list">
          <div v-for="r in g.rows" :key="r.source" class="src-row" :class="{ 'src-row--off': !draftEnabled.has(r.source) }">
            <div class="src-switch-cell">
              <input v-if="r.kinds.length"
                     type="checkbox"
                     class="src-switch"
                     role="switch"
                     :checked="draftEnabled.has(r.source)"
                     :aria-label="`${draftEnabled.has(r.source) ? '停用' : '启用'} ${r.source}`"
                     :title="draftEnabled.has(r.source)
                       ? `点击停用 ${r.source}: 不再从该源刮削任何数据`
                       : `点击启用 ${r.source}: 加入它支持的全部番号类型`"
                     :tabindex="r.controls[0]?.gi === focusIdx ? 0 : -1"
                     :data-ctrl-idx="r.controls[0]?.gi"
                     @click="toggleSource(r.source)"
                     @keydown="onControlKeydown($event, r.controls[0]?.gi)" />
            </div>
            <div class="src-id">
              <code>{{ r.source }}</code>
              <span v-if="r.label" class="src-label">{{ r.label }}</span>
              <span v-if="r.health && (r.health.domain_actual || r.health.domain)"
                    class="src-addr"
                    :class="{ 'src-addr--mismatch': r.health.domain_mismatch }"
                    :title="addrTitle(r.health)">
                {{ r.health.domain_actual || r.health.domain }}
                <template v-if="r.health.domain_mismatch"> · 与实测不符</template>
              </span>
            </div>
            <div class="src-kinds">
              <template v-for="c in r.controls" :key="`${c.type}-${c.kind || ''}`">
                <button v-if="c.type === 'kind'"
                        class="kind-chip"
                        :class="{ 'kind-chip--on': draft[c.kind].includes(r.source) }"
                        :aria-pressed="draft[c.kind].includes(r.source)"
                        :title="`${draft[c.kind].includes(r.source) ? '点击停用' : '点击启用'}${r.source} 参与${KIND_LABEL[c.kind]}番号刮削`"
                        :tabindex="c.gi === focusIdx ? 0 : -1"
                        :data-ctrl-idx="c.gi"
                        @click="toggleKind(r.source, c.kind)"
                        @keydown="onControlKeydown($event, c.gi)">
                  {{ KIND_LABEL[c.kind] }}
                </button>
              </template>
            </div>
            <div class="src-status">
              <template v-if="r.health">
                <span class="pill" :class="`pill--${statusType(r.health)}`">{{ r.health.status_text }}</span>
                <span v-if="r.health.active && !r.health.usable && r.health.unusable_reason"
                      class="src-note">{{ r.health.unusable_reason }}</span>
                <span v-else-if="r.health.breaker === 'open'" class="src-note">
                  连续失败已熔断, {{ Math.round(r.health.cooldown_remaining) }}s 后自动重试
                </span>
                <span v-else-if="r.health.elapsed_ms" class="src-note">
                  {{ r.health.elapsed_ms }} ms
                  <template v-if="r.health.hit_rate !== null && r.health.hit_rate !== undefined">
                    · 命中 {{ Math.round(r.health.hit_rate) }}%
                  </template>
                </span>
              </template>
              <span v-else class="src-note">未探活</span>
            </div>
          </div>
        </div>
      </section>
    </div>

    <div v-if="!rows.length && !loading" class="empty">
      <p class="empty-title">暂无渠道数据</p>
      <p>点「刷新」重新加载</p>
    </div>
  </div>
</template>

<style scoped>
/* 未保存操作栏: 贴在工具栏上方, 一眼看见"有东西没保存" */
.save-bar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  margin-bottom: var(--space-3);
  background: var(--color-warning-soft);
  border: 1px solid var(--color-warning);
  border-radius: var(--radius-md);
}
.save-bar-text { flex: 1; font-size: var(--font-sm); color: var(--color-warning); font-weight: 500; }

.toolbar-label { font-size: var(--font-sm); color: var(--color-text-tertiary); }
.preset-group { display: inline-flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.btn--danger-ghost { color: var(--color-danger); }
.btn--danger-ghost:hover:not(:disabled) { background: var(--color-danger-soft); }

/* 分组 */
.group-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  margin-bottom: var(--space-2);
}
.group-title { font-size: var(--font-md); font-weight: 600; }
.group-hint { font-size: var(--font-sm); color: var(--color-text-tertiary); }

/* 行: 桌面四列(总开关 / 源信息 / 类别 chip / 状态), 窄屏折叠为卡片 */
.src-list { display: flex; flex-direction: column; gap: 1px; }
.src-row {
  display: grid;
  grid-template-columns: 44px minmax(150px, 1fr) minmax(140px, 1fr) minmax(200px, 1.2fr);
  grid-template-areas: "sw id kinds status";
  gap: var(--space-3);
  align-items: center;
  padding: var(--space-2) var(--space-3);
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
}
.src-row:hover { background: var(--color-bg-hover); }
.src-row--off { opacity: 0.62; }
.src-row--off:hover { opacity: 1; }

.src-switch-cell { grid-area: sw; display: flex; align-items: center; }
.src-id { grid-area: id; display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.src-id code { font-family: var(--font-mono); font-size: var(--font-sm); }
.src-label { font-size: var(--font-xs); color: var(--color-text-tertiary); }
.src-addr { font-size: var(--font-xs); color: var(--color-text-tertiary); font-family: var(--font-mono); word-break: break-all; }
.src-addr--mismatch { color: var(--color-danger); }

/* 总开关: 与设置页同一视觉语言(appearance 清零后自绘的滑动开关) */
.src-switch {
  appearance: none;
  -webkit-appearance: none;
  position: relative;
  flex-shrink: 0;
  width: 34px;
  height: 19px;
  border-radius: 999px;
  background: var(--color-bg-subtle);
  border: 1.5px solid var(--color-border-medium);
  cursor: pointer;
  transition: background var(--transition-fast), border-color var(--transition-fast);
}
.src-switch::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  background: var(--color-text-tertiary);
  transition: transform var(--transition-fast), background var(--transition-fast);
}
.src-switch:checked {
  background: var(--color-primary);
  border-color: var(--color-primary);
}
.src-switch:checked::after {
  transform: translateX(15px);
  background: #fff;
}
.src-switch:focus-visible { outline: none; box-shadow: var(--focus-ring); }

.src-kinds { grid-area: kinds; display: flex; flex-wrap: wrap; gap: var(--space-2); }
.src-idle { font-size: var(--font-sm); color: var(--color-text-tertiary); }

/* 类别 chip: 真 button(键盘可达), 点亮 = 入该类清单 */
.kind-chip {
  padding: 3px var(--space-3);
  font-family: inherit;
  font-size: var(--font-sm);
  color: var(--color-text-tertiary);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border-medium);
  border-radius: 999px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.kind-chip:hover { background: var(--color-bg-hover); color: var(--color-text-secondary); }
.kind-chip--on {
  color: var(--color-primary);
  background: var(--color-primary-soft);
  border-color: var(--color-primary);
  font-weight: 500;
}
.kind-chip:focus-visible { outline: none; box-shadow: var(--focus-ring); }

.src-status { grid-area: status; display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); min-width: 0; }
.src-note { font-size: var(--font-xs); color: var(--color-text-tertiary); }

/* 视图筛选器(沿用原渠道监控页样式) */
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

/* 窄屏: 行折叠为卡片。开关与源名同一行, chip 与状态各占整行 */
@media (max-width: 720px) {
  .src-row { grid-template-columns: 44px 1fr; grid-template-areas: "sw id" "kinds kinds" "status status"; }
  .src-status { justify-content: flex-start; }
  .save-bar { flex-wrap: wrap; }
}
</style>
