<script setup>
/** 单部查询: 输入番号临时刮削, 不影响批量队列 */
import { ref, computed, inject } from 'vue'
import * as api from '../api'

const emit = defineEmits(['navigate'])
// 确认对话框由 App.vue provide 注入(与「整理落盘」页同一实例, 文案模板也保持一致)
const askConfirm = inject('javsp:confirm', null)

const avid = ref('')
const running = ref(false)
const progress = ref([])
const info = ref(null)
const sources = ref(null)
const msg = ref('')
const msgType = ref('info')

function rows(s) {
  if (!s || typeof s !== 'object') return []
  return Object.keys(s).map((site) => {
    const x = s[site] || {}
    return {
      site,
      dvdid: x.dvdid || '—',
      title: x.title || '—',
      cover: !!x.has_cover,
      genre: !!x.has_genre,
      actress: !!x.has_actress,
      contributed: !!x.contributed,
      breaker: x.breaker || '',
      healthText: x.health_text || '',
    }
  })
}

// 抓取过程的状态映射: 后端 progress_cb(core.py) 发的是 ('javsp.web.mgstage', 'start')
// 这类模块路径 + 英文状态, 直接展示对用户是噪音。去掉 javsp.web. 前缀, 状态映射为
// 中文短词 + 语义色(命中=绿/未收录=灰/被拦截=红/网络错误=黄), 与「渠道监控」页一致。
const STATUS_MAP = {
  start:         { text: '抓取中', cls: 'pill--info' },
  success:       { text: '命中',   cls: 'pill--success' },
  not_found:     { text: '未收录', cls: 'pill--muted' },
  duplicate:     { text: '重复',   cls: 'pill--muted' },
  blocked:       { text: '被拦截', cls: 'pill--danger' },
  network_error: { text: '网络错误', cls: 'pill--warning' },
  error:         { text: '出错',   cls: 'pill--danger' },
  skipped:       { text: '已跳过', cls: 'pill--muted' },
}
// 提为 computed: 模板里 v-for + 插值会多次调用映射函数, 大列表下是重复构造(m6 教训)
const steps = computed(() => progress.value.map((p, i) => {
  const st = STATUS_MAP[p.status] || { text: p.status || '未知', cls: 'pill--muted' }
  return {
    key: `${p.crawler}-${p.status}-${i}`,
    name: String(p.crawler || '').replace(/^javsp\.web\./, ''),
    text: st.text,
    cls: st.cls,
  }
}))
const sourceRows = computed(() => rows(sources.value))
const contributedCount = computed(() => sourceRows.value.filter((r) => r.contributed).length)

async function run() {
  const q = avid.value.trim()
  if (!q) { msg.value = '请输入番号'; msgType.value = 'warning'; return }
  running.value = true
  progress.value = []
  info.value = null
  sources.value = null
  msg.value = ''
  try {
    await api.scrapeStream({ avid: q }, (d) => {
      if (d.crawler) progress.value = [...progress.value, d]
      if (d.info) { info.value = d.info; sources.value = d.sources || null }
      // 后端错误事件是 {type:'error', msg}, 没有 d.error 字段(2026-10-17 审查 M5)
      if (d.type === 'error') { msg.value = d.msg; msgType.value = 'danger' }
    })
    if (info.value) { msg.value = '刮削完成'; msgType.value = 'info' }
    else if (!msg.value) { msg.value = '未获取到数据(该番号可能未被收录)'; msgType.value = 'warning' }
  } catch (e) {
    msg.value = `查询失败: ${e.message}`
    msgType.value = 'danger'
  } finally {
    running.value = false
  }
}

async function organize() {
  if (!info.value?.guid) { msg.value = '请先成功刮削一部影片'; msgType.value = 'warning'; return }
  // 破坏性操作二次确认(与「整理落盘」页同一文案模板): 原实现点下去就直接写磁盘,
  // 误触代价是真实文件被改名移动, 与整理页逐部确认的策略不一致(设计稿行为分析①)
  const ok = askConfirm
    ? await askConfirm({
        title: '确认写入磁盘',
        impact: `${info.value.dvdid || info.value.cid || '—'} — 将重命名文件、移动目录并生成 NFO 与封面`,
        detail: [
          '原始文件位置会改变, 且不会自动回滚。',
          '已存在的同名文件会被跳过(不会覆盖)。',
        ],
        confirmText: '确认写入',
      })
    : false
  if (!ok) return
  running.value = true
  try {
    await api.organizeStream(info.value.guid, (d) => {
      // 后端 organize 协议: {type:'stage'}/{type:'result', result}/{type:'error', msg},
      // 没有 d.done/d.error 字段(2026-10-17 审查 M5)
      if (d.type === 'stage') { msg.value = '正在整理…'; msgType.value = 'info' }
      if (d.type === 'result') { msg.value = '已整理到磁盘'; msgType.value = 'info' }
      if (d.type === 'error') { msg.value = d.msg || '整理失败'; msgType.value = 'danger' }
    })
  } catch (e) {
    msg.value = `整理失败: ${e.message}`
    msgType.value = 'danger'
  } finally {
    running.value = false
  }
}
</script>

<template>
  <div>
    <div class="page-head">
      <h1 class="page-title">单部查询</h1>
      <p class="page-desc">临时刮削单部影片, 用于验证番号是否收录</p>
    </div>

    <div class="section">
      <div class="toolbar">
        <input
          v-model="avid"
          class="input"
          style="flex: 1; min-width: 200px;"
          placeholder="番号, 如 IPX-177或 cid:sqte00300"
          @keyup.enter="run"
        />
        <button class="btn btn--primary" :disabled="running" @click="run">
          <span v-if="running" class="spinner" aria-hidden="true"></span>
          {{ running ? '查询中' : '查询' }}
        </button>
      </div>
      <p class="field-hint" style="margin-top: var(--space-2);">
        直接回车也可查询。CID 类番号请带 <code>cid:</code> 前缀。查询不影响队列, 也不会改动任何文件。
      </p>
      <div v-if="msg" class="notice" :class="`notice--${msgType === 'info' ? 'info' : msgType}`"
           style="margin-top: var(--space-3);">{{ msg }}</div>
    </div>

    <div v-if="steps.length" class="section">
      <h2 class="section-title">抓取过程</h2>
      <div class="chips">
        <span v-for="s in steps" :key="s.key" class="pill pill--dot" :class="s.cls">
          {{ s.name }} {{ s.text }}
        </span>
      </div>
    </div>

    <div v-if="info" class="section">
      <div class="toolbar">
        <h2 class="section-title" style="margin: 0;">刮削结果</h2>
        <span class="section-hint">来源合并自 {{ contributedCount }} 个渠道</span>
        <div class="toolbar-spacer"></div>
        <button class="btn" :disabled="running" @click="organize">整理这部到磁盘</button>
      </div>
      <dl class="info-grid">
        <dt>番号</dt><dd><code>{{ info.dvdid || info.cid || '—' }}</code></dd>
        <dt>标题</dt><dd>{{ info.title || '—' }}</dd>
        <dt>发行日期</dt><dd>{{ info.release_date || '—' }}</dd>
        <dt>时长</dt><dd>{{ info.duration || '—' }}</dd>
        <dt>制作商</dt><dd>{{ info.producer || '—' }}</dd>
      </dl>
      <div v-if="info.genre?.length" class="tags">
        <span v-for="g in info.genre" :key="g" class="pill pill--info">{{ g }}</span>
      </div>
    </div>

    <div v-if="sourceRows.length" class="section">
      <h2 class="section-title">各渠道贡献</h2>
      <p class="section-hint">「已跳过」表示该渠道被熔断, 未参与本次抓取</p>
      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th style="width: 110px;">渠道</th>
              <th>站点标题</th>
              <th style="width: 190px;">贡献</th>
              <th style="width: 120px;">状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in sourceRows" :key="r.site">
              <td><code>{{ r.site }}</code></td>
              <td class="dim">{{ r.title }}</td>
              <td>
                <span v-if="r.cover" class="pill pill--success">封面</span>
                <span v-if="r.genre" class="pill pill--warning">分类</span>
                <span v-if="r.actress" class="pill pill--info">女优</span>
                <span v-if="!r.contributed" class="pill pill--muted">无贡献</span>
              </td>
              <td>
                <span v-if="r.breaker === 'open'" class="pill pill--danger">已跳过</span>
                <span v-else-if="r.healthText" class="pill pill--muted">{{ r.healthText }}</span>
                <span v-else class="dim">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chips, .tags { display: flex; flex-wrap: wrap; gap: var(--space-1); }
.tags { margin-top: var(--space-3); }
.dim { color: var(--color-text-secondary); }

.info-grid {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: var(--space-2) var(--space-3);
  margin: 0;
  font-size: var(--font-base);
}

.info-grid dt { color: var(--color-text-secondary); }
.info-grid dd { margin: 0; }
code { font-family: var(--font-mono); font-size: var(--font-sm); }
</style>
