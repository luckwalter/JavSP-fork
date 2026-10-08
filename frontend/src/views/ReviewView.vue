<script setup>
/** 结果审阅: 核对每部影片各渠道抓到了什么, 再决定是否整理落盘
 *  相比旧版: 不必层层展开表格 —— 每部影片的渠道贡献直接平铺, 一眼看出哪个源有用、
 *  哪个被熔断跳过了。这是"信息密度失控"痛点的直接修复。
 */
import { ref, computed, onMounted } from 'vue'
import { persistState } from '../composables/usePersist'

const log = ref(persistState('queueLog', []))
const filter = ref('all')      // all | ok | fail

const items = computed(() => {
  const src = log.value || []
  if (filter.value === 'ok') return src.filter((r) => r.ok)
  if (filter.value === 'fail') return src.filter((r) => !r.ok)
  return src
})

const stats = computed(() => {
  const src = log.value || []
  return {
    total: src.length,
    ok: src.filter((r) => r.ok).length,
    fail: src.filter((r) => !r.ok).length,
  }
})

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
      healthStatus: x.health_status || '',
    }
  })
}

/** 某部影片的有效源数 —— 用于快速判断"数据够不够整理" */
function effective(s) {
  const r = rows(s)
  return r.filter((x) => x.contributed).length
}

onMounted(() => {
  // 队列日志在别处产生, 这里只读; 若为空则提示去刮削
  log.value = persistState('queueLog', [])
})
</script>

<template>
  <div>
    <div class="page-head">
      <h1 class="page-title">结果审阅</h1>
      <p class="page-desc">核对每部影片各渠道抓到的内容, 确认无误后再整理落盘</p>
    </div>

    <div v-if="!log.length" class="section">
      <div class="empty">
        <p class="empty-title">还没有刮削结果</p>
        <p>先到「刮削队列」完成批量刮削</p>
        <button class="btn btn--primary" style="margin-top: var(--space-4);"
                @click="$emit('navigate', 'queue')">前往刮削队列</button>
      </div>
    </div>

    <template v-else>
      <div class="stat-row" style="margin-bottom: var(--space-4);">
        <div class="stat">
          <p class="stat-label">总影片</p>
          <p class="stat-value">{{ stats.total }}</p>
        </div>
        <div class="stat">
          <p class="stat-label">刮削成功</p>
          <p class="stat-value" style="color: var(--color-success);">{{ stats.ok }}</p>
        </div>
        <div class="stat">
          <p class="stat-label">失败</p>
          <p class="stat-value" style="color: var(--color-danger);">{{ stats.fail }}</p>
        </div>
      </div>

      <div class="toolbar">
        <div class="segmented">
          <button class="seg" :class="{ 'seg--on': filter === 'all' }" @click="filter = 'all'">
            全部 {{ stats.total }}
          </button>
          <button class="seg" :class="{ 'seg--on': filter === 'ok' }" @click="filter = 'ok'">
            成功 {{ stats.ok }}
          </button>
          <button class="seg" :class="{ 'seg--on': filter === 'fail' }" @click="filter = 'fail'">
            失败 {{ stats.fail }}
          </button>
        </div>
        <div class="toolbar-spacer"></div>
        <button class="btn btn--sm" @click="$emit('navigate', 'organize')">去整理落盘 →</button>
      </div>

      <div v-for="r in items" :key="r.index" class="review-card">
        <div class="review-head">
          <div class="review-id">
            <code>{{ r.avid }}</code>
            <span class="pill" :class="r.ok ? 'pill--success' : 'pill--danger'">
              {{ r.ok ? '成功' : '失败' }}
            </span>
          </div>
          <div class="review-meta">
            {{ effective(r.sources) }} 个渠道有贡献
          </div>
        </div>
        <p class="review-title">{{ r.title || '（无标题）' }}</p>

        <div v-if="rows(r.sources).length" class="table-wrap" style="margin-top: var(--space-3);">
          <table class="table">
            <thead>
              <tr>
                <th style="width: 100px;">渠道</th>
                <th>站点标题</th>
                <th style="width: 180px;">贡献字段</th>
                <th style="width: 110px;">渠道状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in rows(r.sources)" :key="s.site">
                <td><code>{{ s.site }}</code></td>
                <td class="dim">{{ s.title }}</td>
                <td>
                  <span v-if="s.cover" class="pill pill--success">封面</span>
                  <span v-if="s.genre" class="pill pill--warning">分类</span>
                  <span v-if="s.actress" class="pill pill--info">女优</span>
                  <span v-if="!s.contributed" class="pill pill--muted">无贡献</span>
                </td>
                <td>
                  <span v-if="s.breaker === 'open'" class="pill pill--danger">已跳过</span>
                  <span v-else-if="s.healthText" class="pill pill--muted">{{ s.healthText }}</span>
                  <span v-else class="dim">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="field-hint" style="margin-top: var(--space-2);">
          没有渠道数据(该部未成功抓取, 或全部渠道无贡献)
        </p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.segmented { display: inline-flex; border: 1px solid var(--color-border-medium); border-radius: var(--radius-md); overflow: hidden; }
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

.review-card {
  padding: var(--space-4);
  margin-bottom: var(--space-3);
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
}

.review-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.review-id { display: flex; align-items: center; gap: var(--space-2); }
.review-meta { font-size: var(--font-sm); color: var(--color-text-secondary); }
.review-title { margin-top: var(--space-2); font-size: var(--font-base); color: var(--color-text-primary); }
.dim { color: var(--color-text-secondary); }
code { font-family: var(--font-mono); font-size: var(--font-sm); }
</style>
