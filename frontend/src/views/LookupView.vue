<script setup>
/** 单部查询: 输入番号临时刮削, 不影响批量队列 */
import { ref } from 'vue'
import * as api from '../api'

const emit = defineEmits(['navigate'])

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
      if (d.error) { msg.value = d.error; msgType.value = 'danger' }
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
  running.value = true
  try {
    await api.organizeStream(info.value.guid, (d) => {
      if (d.done) { msg.value = '已整理到磁盘'; msgType.value = 'info' }
      if (d.error) { msg.value = d.error; msgType.value = 'danger' }
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
      <div v-if="msg" class="notice" :class="`notice--${msgType === 'info' ? 'info' : msgType}`"
           style="margin-top: var(--space-3);">{{ msg }}</div>
    </div>

    <div v-if="progress.length" class="section">
      <h2 class="section-title">抓取过程</h2>
      <div class="chips">
        <span v-for="(p, i) in progress" :key="i" class="pill pill--muted">
          {{ p.crawler }}: {{ p.status }}
        </span>
      </div>
    </div>

    <div v-if="info" class="section">
      <div class="toolbar">
        <h2 class="section-title" style="margin: 0;">结果</h2>
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

    <div v-if="rows(sources).length" class="section">
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
            <tr v-for="r in rows(sources)" :key="r.site">
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
