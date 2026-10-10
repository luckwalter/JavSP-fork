<script setup>
/** 整理落盘: 从**已刮削**的影片中选择要落盘的, 写入磁盘(改名/移动/生成 NFO 与封面)
 *
 *  设计要点(v0.2.2 重写, 依据主人反馈):
 *   旧版让用户手工输入番号再刮削 —— 这与「单部查询」功能完全重复, 且在没有任何刮削
 *   结果时显得毫无意义(界面只有"第一步 · 刮削目标影片"的输入框)。
 *   正确形态: 输入源应是**前面已刮削完成的影片列表**, 用户勾选 → 整理。
 *   列表为空时明确指引"请先刮削", 而不是给一个用不上的输入框。
 *
 *  ⚠️ 这是全应用唯一会真正改动文件的地方, 因此:
 *    - 独立成页, 让用户有单独的心智步骤去核对
 *    - 每部都需确认, 明示后果
 *    - 默认不预选任何影片, 必须主动勾选
 */
import { ref, computed, onMounted, inject } from 'vue'
import * as api from '../api'
import { persistState, saveState } from '../composables/usePersist'

const emit = defineEmits(['navigate'])
const askConfirm = inject('javsp:confirm', null)

const movies = ref([])
const selected = ref(persistState('organizeSelection', []))
const loading = ref(false)
const running = ref(false)
const results = ref({})      // guid -> {ok, path, error}
const msg = ref('')
const msgType = ref('info')
const filter = ref('scraped')  // scraped=已刮削待整理 / all=全部

async function load() {
  loading.value = true
  try {
    movies.value = await api.getMovies()
  } catch (e) {
    msg.value = `无法获取影片列表: ${e.message}`
    msgType.value = 'danger'
  } finally {
    loading.value = false
  }
}

const list = computed(() => {
  const src = movies.value || []
  if (filter.value === 'scraped') return src.filter((m) => m.scraped)
  return src
})

const scrapable = computed(() => (movies.value || []).filter((m) => m.scraped))
const allSelected = computed(
  () => list.value.length > 0 && list.value.every((m) => selected.value.includes(m.guid)),
)

function toggleAll() {
  selected.value = allSelected.value ? [] : list.value.map((m) => m.guid)
  saveState('organizeSelection', selected.value)
}

async function organizeOne(movie) {
  const ok = askConfirm
    ? await askConfirm({
        title: '确认写入磁盘',
        impact: `${movie.dvdid || movie.cid} — 将重命名文件、移动目录并生成 NFO 与封面`,
        detail: [
          '原始文件位置会改变, 且不会自动回滚。',
          '已存在的同名文件会被跳过(不会覆盖)。',
        ],
        confirmText: '确认写入',
      })
    : false
  if (!ok) return false

  running.value = true
  try {
      await api.organizeStream(movie.guid, (d) => {
      // 后端 organize 完成事件是 {type:'result'}, 错误是 {type:'error'} —— 没有 d.done 字段
      if (d.type === 'result') {
        const path = (d.result && (d.result.saved_path || d.result.movie_path)) || '完成'
        results.value = { ...results.value, [movie.guid]: { ok: true, path } }
        load()  // 刷新 organized 真实态: 使按钮变"已落盘"并禁用, 避免重复整理
      }
      if (d.type === 'error') {
        results.value = { ...results.value, [movie.guid]: { ok: false, error: d.msg } }
      }
    })
    return true
  } catch (e) {
    results.value = { ...results.value, [movie.guid]: { ok: false, error: e.message } }
    return true
  } finally {
    running.value = false
  }
}

/** 批量整理选中项: 逐部处理, 每部单独确认(可在中途叫停, 避免一次性误操作大量文件) */
async function organizeSelected() {
  const targets = list.value.filter((m) => selected.value.includes(m.guid))
  if (!targets.length) return
  const ok = askConfirm
    ? await askConfirm({
        title: `确认整理 ${targets.length} 部影片`,
        impact: '将逐部重命名文件、移动目录并生成 NFO 与封面',
        detail: [
          '原始文件位置会改变, 且不会自动回滚。',
          '每部会再次单独确认, 可随时中断。',
        ],
        confirmText: '开始整理',
      })
    : false
  if (!ok) return

  for (const m of targets) {
    // organizeOne 返回 false = 用户在单部确认里点了取消 -> 停止后续
    const done = await organizeOne(m)
    if (!done) break
  }
}

const doneCount = computed(() => Object.values(results.value).filter((r) => r.ok).length)
const failCount = computed(() => Object.values(results.value).filter((r) => !r.ok).length)

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <h1 class="page-title">整理落盘</h1>
      <p class="page-desc">
        把已刮削的影片写入磁盘: 重命名文件、移动目录、生成 NFO 与封面
      </p>
    </div>

    <div class="notice notice--warning" style="margin-bottom: var(--space-4); max-width: 760px;">
      此操作会<b>实际改动文件</b>。建议先刮削、到「结果审阅」核对无误后再来整理。
    </div>

    <!-- 空状态: 没刮削过就明确指引, 而不是给一个用不上的输入框 -->
    <div v-if="!loading && !scrapable.length" class="section">
      <div class="empty">
        <p class="empty-title">还没有已刮削的影片</p>
        <p>
          整理落盘需要先有刮削结果。请先到「刮削队列」或「单部查询」完成刮削，
          之后回到这里选择要写入磁盘的影片。
        </p>
        <div class="empty-actions">
          <button class="btn btn--primary" @click="emit('navigate', 'queue')">前往刮削队列</button>
          <button class="btn" @click="emit('navigate', 'lookup')">单部查询</button>
        </div>
      </div>
    </div>

    <template v-else>
      <div class="section">
        <div class="toolbar">
          <div class="segmented">
            <button class="seg" :class="{ 'seg--on': filter === 'scraped' }" @click="filter = 'scraped'">
              待整理 {{ scrapable.length }}
            </button>
            <button class="seg" :class="{ 'seg--on': filter === 'all' }" @click="filter = 'all'">
              全部影片 {{ movies.length }}
            </button>
          </div>
          <div class="toolbar-spacer"></div>
          <button class="btn" :disabled="loading" @click="load">刷新</button>
        </div>

        <div v-if="msg" class="notice" :class="`notice--${msgType === 'info' ? 'info' : msgType}`"
             style="margin-bottom: var(--space-3);">{{ msg }}</div>

        <div v-if="Object.keys(results).length" class="stat-row" style="margin-bottom: var(--space-4);">
          <div class="stat">
            <p class="stat-label">已整理成功</p>
            <p class="stat-value" style="color: var(--color-success);">{{ doneCount }}</p>
          </div>
          <div class="stat">
            <p class="stat-label">失败</p>
            <p class="stat-value" style="color: var(--color-danger);">{{ failCount }}</p>
          </div>
        </div>

        <div class="toolbar">
          <label class="pick-all">
            <input type="checkbox" :checked="allSelected" :disabled="!list.length" @change="toggleAll" />
            <span>全选</span>
          </label>
          <span class="stat-label" style="margin: 0;">已选 {{ selected.length }} 部</span>
          <div class="toolbar-spacer"></div>
          <button class="btn btn--danger" :disabled="!selected.length || running" @click="organizeSelected">
            <span v-if="running" class="spinner" aria-hidden="true"></span>
            整理选中项
          </button>
        </div>

        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th style="width: 34px;"></th>
                <th style="width: 140px;">番号</th>
                <th>文件</th>
                <th style="width: 90px;">状态</th>
                <th style="width: 110px;">整理结果</th>
                <th style="width: 90px;"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="m in list" :key="m.guid">
                <td>
                  <input
                    v-if="m.scraped"
                    type="checkbox"
                    :value="m.guid"
                    v-model="selected"
                    :aria-label="`选择 ${m.dvdid || m.cid}`"
                  />
                  <span v-else class="dim">-</span>
                </td>
                <td><code>{{ m.dvdid || m.cid }}</code></td>
                <td class="file-cell">{{ (m.files || [])[0] || '—' }}</td>
                <td>
                  <span class="pill" :class="m.scraped ? 'pill--success' : 'pill--muted'">
                    {{ m.scraped ? '已刮削' : '未刮削' }}
                  </span>
                </td>
                <td>
                  <template v-if="results[m.guid]">
                    <span v-if="results[m.guid].ok" class="pill pill--success">已写入</span>
                    <span v-else class="pill pill--danger" :title="results[m.guid].error">失败</span>
                  </template>
                  <span v-else-if="m.organized" class="pill pill--success">已落盘</span>
                  <span v-else class="dim">—</span>
                </td>
                <td>
                  <button
                    v-if="m.scraped && !results[m.guid]?.ok && !m.organized"
                    class="btn btn--sm btn--danger"
                    :disabled="running"
                    @click="organizeOne(m)"
                  >整理</button>
                  <span v-else-if="results[m.guid]?.ok || m.organized" class="pill pill--success">已落盘</span>
                  <span v-else class="dim">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="field-hint" style="margin-top: var(--space-3);">
          「未刮削」的影片需先刮削才能整理 —— 勾选框已置灰。
        </p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.empty-actions {
  display: flex;
  gap: var(--space-2);
  justify-content: center;
  margin-top: var(--space-4);
}

.pick-all {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-sm);
  cursor: pointer;
}

.file-cell { color: var(--color-text-secondary); }
.dim { color: var(--color-text-tertiary); }

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

code { font-family: var(--font-mono); font-size: var(--font-sm); }
</style>
