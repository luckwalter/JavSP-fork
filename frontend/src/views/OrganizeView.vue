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
 *    - 明示后果; 批量时**一次确认后连续处理**, 不再逐部弹框(见下)
 *    - 默认不预选任何影片, 必须主动勾选
 *
 *  批量确认方式(v0.3.12, 依据 issue #3: 批量整理时逐部弹「确认写入磁盘」太啰嗦):
 *    - 默认「一次确认完成」: 批量入口确认一次后连续处理全部选中影片;
 *      护栏由**「停止」按钮**承担(处理中可随时中断, 剩余的不再执行)。
 *    - 可在设置页切回「逐部确认」(谨慎模式), 或在批量确认框里临时勾选
 *      「本次不再逐部确认」—— 一次性覆盖, 不改长期偏好。
 *    - 单部「整理」按钮**始终**逐次确认(防手滑)。
 */
import { ref, computed, onMounted, inject, watch } from 'vue'
import * as api from '../api'
import { persistState, saveState, persistLocal, saveLocal } from '../composables/usePersist'
import { useTask } from '../composables/useTask'

const emit = defineEmits(['navigate'])
const { taskStart, taskProgress, taskFinish } = useTask()
const askConfirm = inject('javsp:confirm', null)

const movies = ref([])
const selected = ref(persistState('organizeSelection', []))
const loading = ref(false)
const running = ref(false)
const results = ref({})      // guid -> {ok, path, error}
const msg = ref('')
const msgType = ref('info')
const filter = ref('scraped')  // scraped=已刮削待整理 / all=全部

// 批量整理的确认方式(长期偏好, 存 localStorage —— 这是用户的操作习惯, 不是会话内临时结果):
//   true  = 一次确认完成(默认)
//   false = 逐部确认(谨慎模式; 批量确认框里可临时勾选「本次不再逐部确认」)
const confirmAllAtOnce = ref(persistLocal('organizeConfirmAllAtOnce', true))
watch(confirmAllAtOnce, (v) => saveLocal('organizeConfirmAllAtOnce', v))

const batchRunning = ref(false)    // 批量整理进行中
const stopRequested = ref(false)   // 用户点了「停止」—— 当前这部做完即止
const batchProgress = ref({ done: 0, total: 0 })

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

/**
 * 整理单部影片。
 * opts.skipConfirm   —— 上层已确认过, 直接落盘(批量一次确认模式)
 * opts.manageRunning —— 是否由本函数管理 running 标志(批量时由上层统一管理)
 * 返回 false = 用户取消(未执行), 上层据此停止后续
 */
async function organizeOne(movie, opts = {}) {
  const { skipConfirm = false, manageRunning = true } = opts
  // 结果审阅页可能对某部关闭了"落盘时使用此翻译"(按 avid 记录), 此处传
  // use_translate=false, 后端落盘时还原刮削阶段译文对应的原文;
  // 未记录(含未配置翻译)时传 null, 由服务端按配置决定。
  let useTranslate = null
  try {
    const set = new Set(JSON.parse(localStorage.getItem('javsp:reviewNoTranslate') || '[]'))
    const key = movie.dvdid || movie.cid || movie.avid
    if (set.has(key)) useTranslate = false
  } catch { /* localStorage 异常时按默认(按配置)处理 */ }
  if (!skipConfirm) {
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
  }

  if (manageRunning) running.value = true
  try {
      await api.organizeStream(movie.guid, (d) => {
      // 后端 organize 完成事件是 {type:'result'}, 错误是 {type:'error'} —— 没有 d.done 字段
      if (d.type === 'result') {
        const path = (d.result && (d.result.saved_path || d.result.movie_path)) || '完成'
        // leftover_dirs: 源目录已空但自动删除失败(被占用/权限), 收集后提示用户手动处理
        const leftover = (d.result && d.result.leftover_dirs) || []
        results.value = { ...results.value, [movie.guid]: { ok: true, path, leftover } }
        load()  // 刷新 organized 真实态: 使按钮变"已落盘"并禁用, 避免重复整理
      }
      if (d.type === 'error') {
        results.value = { ...results.value, [movie.guid]: { ok: false, error: d.msg } }
      }
    }, useTranslate)
    return true
  } catch (e) {
    results.value = { ...results.value, [movie.guid]: { ok: false, error: e.message } }
    return true
  } finally {
    if (manageRunning) running.value = false
  }
}

/**
 * 批量整理选中项: 一次确认后连续处理(v0.3.12 改, 依据 issue #3)
 *  - 默认「一次确认完成」: 不再逐部弹「确认写入磁盘」; 护栏改为处理中的「停止」按钮
 *  - 谨慎模式(设置页关闭): 仍逐部确认; 确认框里可临时勾选「本次不再逐部确认」
 */
async function organizeSelected() {
  const targets = list.value.filter((m) => selected.value.includes(m.guid))
  if (!targets.length) return
  const perItem = !confirmAllAtOnce.value
  const res = askConfirm
    ? await askConfirm({
        title: `确认整理 ${targets.length} 部影片`,
        impact: '将重命名文件、移动目录并生成 NFO 与封面',
        detail: [
          '原始文件位置会改变, 且不会自动回滚。',
          perItem
            ? '每部会再次单独确认, 可随时中断。'
            : '将一次性处理全部选中影片, 过程中可随时点「停止」中断。',
        ],
        confirmText: '开始整理',
        option: perItem ? { label: '本次不再逐部确认（一次处理全部）', checked: false } : null,
      })
    : false
  if (!res) return
  // 逐部模式下, 勾了复选框则本次不再逐部问; 一次确认模式本来就跳过单部确认
  const skipPerItem = perItem ? !!(res.option) : true

  batchRunning.value = true
  stopRequested.value = false
  batchProgress.value = { done: 0, total: targets.length }
  running.value = true             // 批量期间禁用行内按钮, 避免并发落盘
  taskStart(targets.length)        // 同步全局任务进度(底部状态栏常驻可见)
  try {
    for (let i = 0; i < targets.length; i++) {
      if (stopRequested.value) break
      const m = targets[i]
      taskProgress(i + 1, m.avid || m.title || '')
      // organizeOne 返回 false = 用户在单部确认里点了取消 -> 停止后续
      const done = await organizeOne(m, { skipConfirm: skipPerItem, manageRunning: false })
      if (!done) break
      batchProgress.value = { done: i + 1, total: targets.length }
    }
  } finally {
    taskFinish()                   // 中断/异常也清理, 不让状态栏卡在假进度上
    batchRunning.value = false
    stopRequested.value = false
    running.value = false
  }
}

/** 用户点「停止」: 当前这部做完即止, 剩余的不再执行 */
function stopBatch() {
  stopRequested.value = true
}

const doneCount = computed(() => Object.values(results.value).filter((r) => r.ok).length)
const failCount = computed(() => Object.values(results.value).filter((r) => !r.ok).length)
// 本次整理中未能自动删除的空源目录(被 SMB 占用/权限不足等), 提示用户手动清理
const leftoverDirs = computed(() => {
  const all = []
  for (const r of Object.values(results.value)) {
    if (r.ok && r.leftover && r.leftover.length) all.push(...r.leftover)
  }
  return all
})

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

        <!-- 源目录已空但自动删除失败(常见: Windows 桌面版经 SMB 整理时资源管理器
             正停在该目录, 或权限不足): 列出路径供用户确认后手动删除 -->
        <div v-if="leftoverDirs.length" class="notice notice--warning"
             style="margin-bottom: var(--space-4); max-width: 760px;">
          <p style="margin: 0 0 var(--space-2);">
            <strong>{{ leftoverDirs.length }} 个源目录已空但未能自动删除</strong>
            （可能被程序占用或权限不足），确认无用后可手动删除：
          </p>
          <ul style="margin: 0; padding-left: 1.4em; line-height: 1.7;">
            <li v-for="(item, i) in leftoverDirs" :key="i">
              <code>{{ item.path }}</code>
            </li>
          </ul>
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
            {{ running ? '处理中…' : '整理选中项' }}
          </button>
        </div>

        <!-- 批量进行中: 显示进度并提供「停止」—— 替代原先"逐部确认"承担的护栏 -->
        <div v-if="batchRunning" class="notice notice--info batch-bar">
          <span>正在整理 {{ batchProgress.done }}/{{ batchProgress.total }} 部…</span>
          <button class="btn btn--sm" @click="stopBatch">停止</button>
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

.batch-bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

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
