<script setup>
/** 影片库: 选择目录 → 扫描 → 选择影片
 *  相比旧版: 扫描路径与结果会持久化(刷新可接续); 选中状态有明确视觉反馈。
 */
import { ref, watch, onMounted } from 'vue'
import * as api from '../api'
import { persistState, saveState, clearState } from '../composables/usePersist'

const props = defineProps({
  /** 允许浏览的根(由 App 登录后拉取)。用作扫描路径的默认值 */
  browseRoot: { type: String, default: '' },
})
const emit = defineEmits(['navigate'])

const scanPath = ref(persistState('scanPath', ''))
const movies = ref(persistState('movies', []))
const selectedGuids = ref([])
const msg = ref('')
const msgType = ref('info')
const scanning = ref(false)
const browserVisible = ref(false)
const browseLoading = ref(false)
const browseCurrent = ref('')
const browseParent = ref(null)
const browseDirs = ref([])
const browseRoot = ref('')

watch([scanPath, movies], () => {
  saveState('scanPath', scanPath.value)
  // 影片列表可能很大(含 files 数组), 只持久化表格需要的字段, 避免撑爆 sessionStorage
  saveState(
    'movies',
    movies.value.map((m) => ({
      guid: m.guid,
      dvdid: m.dvdid,
      cid: m.cid,
      data_src: m.data_src,
      scraped: m.scraped,
      fileName: (m.files && m.files[0]) || '',
      fileCount: (m.files || []).length,
    })),
  )
}, { deep: true })

function setMsg(text, type = 'info') {
  msg.value = text
  msgType.value = type
}

async function doScan() {
  if (!scanPath.value.trim()) {
    setMsg('请先填写影片目录路径', 'warning')
    return
  }
  scanning.value = true
  setMsg('正在扫描…')
  try {
    const d = await api.scan(scanPath.value.trim())
    movies.value = d.movies || []
    selectedGuids.value = []
    if (!movies.value.length) {
      setMsg('未发现影片。可尝试调整目录, 或确认文件类型与最小体积限制(设置页可改)', 'warning')
    } else {
      setMsg(`发现 ${d.count} 部影片`)
    }
  } catch (e) {
    setMsg(`扫描失败: ${e.message}`, 'danger')
  } finally {
    scanning.value = false
  }
}

function toggleAll() {
  selectedGuids.value =
    selectedGuids.value.length === movies.value.length ? [] : movies.value.map((m) => m.guid)
}

function isAllSelected() {
  return movies.value.length > 0 && selectedGuids.value.length === movies.value.length
}

// ---- 目录选择器 ----
async function openBrowser() {
  browserVisible.value = true
  await loadBrowse('')
}

async function loadBrowse(path) {
  browseLoading.value = true
  try {
    const d = await api.browse(path || '')
    browseCurrent.value = d.current
    browseParent.value = d.parent
    browseDirs.value = d.dirs || []
    browseRoot.value = d.root
    if (!scanPath.value) scanPath.value = d.current
  } catch (e) {
    setMsg(`无法打开目录: ${e.message}`, 'danger')
    browserVisible.value = false
  } finally {
    browseLoading.value = false
  }
}

function chooseDir(p) {
  scanPath.value = p
  browserVisible.value = false
}

function confirmBrowse() {
  scanPath.value = browseCurrent.value
  browserVisible.value = false
}

// 跳转前把勾选的影片写入队列存储: 否则 QueueView 的 guids 永远为空、
// 「仅刮削」按钮保持 disabled、刮削请求根本不会发出(实测表现=点了没反应)。
function goQueue() {
  saveState('queueGuids', selectedGuids.value)
  emit('navigate', 'queue')
}

onMounted(() => {
  // 默认目录指向允许浏览的根(通常是 /data, 即容器内媒体目录)。
  // 已有持久化值或已选过则不覆盖 —— 用户的选择优先。
  if (!scanPath.value) {
    if (props.browseRoot) {
      scanPath.value = props.browseRoot
    } else {
      // App 尚未拉到根时自行问一次(免登录状态下 browse 也不需要登录)
      api.browse('').then((d) => { browseRoot.value = d.root }).catch(() => {})
    }
  }
})

// 暴露给父组件/测试
defineExpose({ scanPath, movies, selectedGuids, doScan })
</script>

<template>
  <div>
    <div class="page-head">
      <h1 class="page-title">影片库</h1>
      <p class="page-desc">选择媒体目录并扫描, 然后选择要刮削的影片</p>
    </div>

    <!-- 目录选择 -->
    <div class="section">
      <div class="toolbar">
        <button class="btn" @click="openBrowser">浏览目录…</button>
        <input
          v-model="scanPath"
          class="input"
          style="flex: 1; min-width: 240px;"
          placeholder="影片目录的绝对路径, 如 /data/movies"
          @keyup.enter="doScan"
        />
        <button class="btn btn--primary" :disabled="scanning" @click="doScan">
          <span v-if="scanning" class="spinner" aria-hidden="true"></span>
          {{ scanning ? '扫描中' : '扫描' }}
        </button>
      </div>
      <p class="field-hint">
        路径必须是<b>容器内</b>的路径(挂载点), 不是 NAS 真机路径。点「浏览目录」最稳妥。
      </p>

      <div v-if="msg" class="notice" :class="`notice--${msgType === 'info' ? 'info' : msgType}`"
           style="margin-top: var(--space-3);">
        {{ msg }}
      </div>
    </div>

    <!-- 结果 -->
    <div v-if="movies.length" class="section">
      <div class="toolbar">
        <label class="pick-all">
          <input type="checkbox" :checked="isAllSelected()" @change="toggleAll" />
          <span>全选</span>
        </label>
        <span class="stat-label" style="margin: 0;">
          共 {{ movies.length }} 部 · 已选 {{ selectedGuids.length }} 部
        </span>
        <div class="toolbar-spacer"></div>
        <button class="btn" :disabled="!selectedGuids.length"
                @click="goQueue">
          去刮削 ({{ selectedGuids.length }})
        </button>
      </div>

      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th style="width: 34px;"></th>
              <th style="width: 140px;">番号</th>
              <th style="width: 120px;">类型</th>
              <th>文件</th>
              <th style="width: 90px;">状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in movies" :key="m.guid">
              <td>
                <input
                  type="checkbox"
                  :value="m.guid"
                  v-model="selectedGuids"
                  :aria-label="`选择 ${m.dvdid || m.cid}`"
                />
              </td>
              <td><code>{{ m.dvdid || m.cid }}</code></td>
              <td>
                <span class="pill pill--muted">{{ m.data_src }}</span>
              </td>
              <td class="file-cell">
                {{ m.fileName || m.files?.[0] || '—' }}
                <span v-if="m.fileCount > 1" class="pill pill--muted" style="margin-left: 6px;">
                  +{{ m.fileCount - 1 }}
                </span>
              </td>
              <td>
                <span class="pill" :class="m.scraped ? 'pill--success' : 'pill--muted'">
                  {{ m.scraped ? '已刮削' : '未刮削' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-else-if="!msg" class="empty">
      <p class="empty-title">还没有扫描结果</p>
      <p>选择媒体目录后点「扫描」开始</p>
    </div>

    <!-- 目录选择对话框 -->
    <div v-if="browserVisible" class="overlay" @click.self="browserVisible = false">
      <div class="dialog" role="dialog" aria-modal="true" aria-label="选择目录">
        <h2 class="dialog-title">选择影片目录</h2>
        <p class="dialog-path" :title="browseCurrent">{{ browseCurrent }}</p>

        <div class="browser-list">
          <div v-if="browseLoading" class="empty">加载中…</div>
          <template v-else>
            <button v-if="browseParent" class="browser-item" @click="loadBrowse(browseParent)">
              <span class="browser-name">← 返回上一层</span>
            </button>
            <div v-if="!browseDirs.length && !browseParent" class="empty">此目录下没有子目录</div>
            <button
              v-for="d in browseDirs"
              :key="d.name"
              class="browser-item"
              @click="loadBrowse(d.path)"
              @dblclick="chooseDir(d.path)"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
              </svg>
              <span class="browser-name">{{ d.name }}</span>
            </button>
          </template>
        </div>

        <p class="field-hint">单击进入, 双击选中当前目录; 或直接用下方按钮确认当前路径</p>
        <div class="dialog-actions">
          <button class="btn" @click="browserVisible = false">取消</button>
          <button class="btn btn--primary" @click="confirmBrowse">选择当前目录</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pick-all {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-sm);
  cursor: pointer;
}

.file-cell { color: var(--color-text-secondary); }

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
  max-width: 520px;
  padding: var(--space-5);
  background: var(--color-bg-surface);
  border-radius: var(--radius-lg);
}

.dialog-title { font-size: var(--font-lg); font-weight: 600; margin-bottom: var(--space-2); }

.dialog-path {
  padding: var(--space-2) var(--space-3);
  margin-bottom: var(--space-3);
  font-family: var(--font-mono);
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.browser-list {
  max-height: 320px;
  overflow-y: auto;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  margin-bottom: var(--space-2);
}

.browser-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  padding: var(--space-2) var(--space-3);
  font-family: inherit;
  font-size: var(--font-base);
  text-align: left;
  color: var(--color-text-primary);
  background: none;
  border: 0;
  border-bottom: 1px solid var(--color-border-light);
  cursor: pointer;
}

.browser-item:last-child { border-bottom: none; }
.browser-item:hover { background: var(--color-bg-hover); }
.browser-item:focus-visible { outline: none; box-shadow: inset var(--focus-ring); }
.browser-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.dialog-actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
</style>
