<template>
  <div class="naming-builder" :class="{ 'naming-builder--disabled': disabled }">
    <!-- 头部: 字段标签 + 可视化/源码 分段切换 -->
    <div class="builder-head">
      <span class="builder-label">{{ label }}</span>
      <div class="seg" role="tablist" :aria-label="`${label} 编辑方式`">
        <button type="button" role="tab" :aria-selected="mode === 'visual'"
                :class="['seg-btn', { 'seg-btn--on': mode === 'visual' }]"
                @click="mode = 'visual'">可视化构建</button>
        <button type="button" role="tab" :aria-selected="mode === 'source'"
                :class="['seg-btn', { 'seg-btn--on': mode === 'source' }]"
                @click="switchToSource">文本源码</button>
      </div>
    </div>

    <!-- ============ 可视化构建 ============ -->
    <template v-if="mode === 'visual'">
      <div class="builder-body">
        <!-- 左栏: 变量面板 + 符号 + 自定义文本 -->
        <div class="var-panel">
          <p class="var-panel-tip">点击元素添加到 <b>L{{ activeLayer + 1 }}</b> 层</p>
          <div class="var-grid">
            <button v-for="v in NAMING_VARS" :key="v.key" type="button" class="var-chip"
                    :title="`${v.desc} · ${'{' + v.key + '}'}`"
                    @click="appendVar(v.key)">
              <span class="var-chip-label">{{ v.label }}</span>
              <span class="var-chip-key">{{ '{' + v.key + '}' }}</span>
            </button>
            <button type="button" class="var-chip var-chip--custom" @click="appendCustomText">
              <span class="var-chip-label">＋ 自定义文本…</span>
            </button>
          </div>
          <div class="symbol-row">
            <span class="symbol-row-label">常用符号</span>
            <button v-for="s in COMMON_SYMBOLS" :key="s" type="button" class="symbol-btn"
                    :title="`插入符号「${s}」`" @click="appendText(s)">{{ s === ' ' ? '␣' : s }}</button>
          </div>
        </div>

        <!-- 中栏: 层级树 + 层内 chips 流 -->
        <div class="layer-tree">
          <div v-for="(layer, li) in layers" :key="li" class="layer-row"
               :class="{ 'layer-row--active': li === activeLayer, 'layer-row--dragover': dropTarget && dropTarget.layer === li && dropTarget.index === -1 }"
               @click="activeLayer = li"
               @dragover.prevent="onLayerDragOver(li)"
               @drop.prevent="onLayerDrop(li)">
            <span class="layer-index" :title="single ? '当前层' : `第 ${li + 1} 层`">{{ single ? '·' : 'L' + (li + 1) }}</span>
            <div class="token-flow">
              <template v-for="(t, ti) in layer" :key="ti">
                <span class="token-chip" :class="[
                        'token--' + t.type,
                        { 'token--unknown': t.type === 'var' && unknown.includes(t.value),
                          'token--dragging': dragFrom && dragFrom.layer === li && dragFrom.index === ti,
                          'token--drop-before': dropTarget && dropTarget.layer === li && dropTarget.index === ti && dropTarget.pos === 'before' && !(dragFrom && dragFrom.layer === li && dragFrom.index === ti),
                          'token--drop-after': dropTarget && dropTarget.layer === li && dropTarget.index === ti && dropTarget.pos === 'after' && !(dragFrom && dragFrom.layer === li && dragFrom.index === ti) }]"
                      draggable="true"
                      :title="t.type === 'var' ? `变量 ${'{' + t.value + '}'} · 可拖动` : `文本「${t.value}」 · 可拖动`"
                      @dragstart="onTokenDragStart(li, ti)"
                      @dragover.prevent="onTokenDragOver(li, ti, $event)"
                      @drop.prevent.stop="onTokenDrop(li, ti)"
                      @dragend="onTokenDragEnd">
                  <span class="token-label">{{ t.type === 'var' ? labelOf(t.value) : t.value }}</span>
                  <button type="button" class="token-del" title="删除此元素"
                          @click.stop="removeToken(li, ti)">✕</button>
                </span>
              </template>
              <span v-if="!layer.length" class="token-empty">空层级, 点击左侧元素填入</span>
            </div>
            <div class="layer-ops" v-if="!single || layer.length">
              <button type="button" class="layer-op" title="上移一层" :disabled="li === 0"
                      @click.stop="moveLayer(li, -1)">↑</button>
              <button type="button" class="layer-op" title="下移一层" :disabled="li === layers.length - 1"
                      @click.stop="moveLayer(li, 1)">↓</button>
              <button type="button" class="layer-op layer-op--del" title="删除此层"
                      v-if="!single" @click.stop="removeLayer(li)">✕</button>
            </div>
          </div>
          <button v-if="!single" type="button" class="layer-add" @click="addLayer">＋ 新建目录层级</button>
          <p v-if="!single" class="layer-hint">层与层之间以 <code>/</code> 分隔形成目录; 层内元素可直接拖动排序</p>
        </div>

        <!-- 右栏: 实时预览 -->
        <div class="builder-preview">
          <p class="preview-title">实时预览</p>
          <p class="preview-path">{{ rendered || '(空)' }}</p>
          <div class="preview-meter" :class="{ 'preview-meter--over': overLimit }">
            <div class="preview-meter-bar" :style="{ width: meterPct + '%' }"></div>
          </div>
          <p class="preview-length" :class="{ 'preview-length--over': overLimit }">
            长度 {{ measured }} / {{ lengthMax || '∞' }} {{ byByte ? '字节' : '字符' }}
          </p>
          <p v-if="unknown.length" class="preview-alert preview-alert--danger">
            ⚠ 未知占位符 {{ unknown.map((u) => '{' + u + '}').join(' ') }}: 后端无法渲染, 整理时会报错, 请删除或改为左侧已有变量
          </p>
          <p v-else-if="emptyLayer" class="preview-alert preview-alert--warning">⚠ 存在空层级, 会产生连续 // 或末尾 / 的异常路径</p>
          <p v-else class="preview-alert preview-alert--ok">✓ 占位符全部有效</p>
        </div>
      </div>
    </template>

    <!-- ============ 文本源码(高级逃生门) ============ -->
    <template v-else>
      <div class="source-body">
        <textarea v-model="sourceDraft" class="source-input" rows="3" spellcheck="false"
                  :placeholder="single ? '如 {num} {title}' : '如 {num}/{actress}/{num} {title}'"></textarea>
        <div class="source-foot">
          <span class="source-tip">源码与可视化构建实时等价, 修改后点「应用」生效</span>
          <button type="button" class="btn btn--primary btn--sm" @click="commitSource">应用源码</button>
        </div>
      </div>
    </template>

    <!-- 快速模板 -->
    <div class="preset-row">
      <span class="preset-label">快速模板</span>
      <button v-for="p in PATTERN_PRESETS" :key="p.name" type="button" class="preset-btn"
              :title="p.pattern" @click="applyPreset(p)">{{ p.name }}</button>
    </div>

    <!-- 撤销 toast -->
    <transition name="undo-toast">
      <div v-if="undoToast" class="undo-toast" role="status">
        <span>{{ undoToast.message }}</span>
        <button type="button" class="undo-btn" @click="performUndo">撤销</button>
        <span class="undo-count">{{ undoToast.remain }}s</span>
      </div>
    </transition>
  </div>
</template>

<script setup>
/**
 * 命名规则可视化构建器
 * ---------------------------------------------------------------------------
 * 解决什么问题: 旧版让用户手打 {num}/{actress} 模板 —— 普通用户记不住变量名,
 * 打错(如 {studio} 这个不存在的变量)会直接炸整理。本组件把「打字」换成
 * 「点选 + 拖动」, 中文标签展示, 源码名降级为角标, 但保留文本源码逃生门。
 *
 * 与后端契约: 序列化结果就是 core.py 里 str.format(**copyd) 的模板;
 * 未知占位符会 KeyError, 因此这里实时校验(见 utils/namingPattern.js)。
 */
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import {
  NAMING_VARS, COMMON_SYMBOLS, PATTERN_PRESETS,
  parsePattern, tokenizeSegment, serializeLayers,
  findUnknownVars, renderPattern, measureLength, hasEmptyLayer,
} from '../utils/namingPattern'

const props = defineProps({
  modelValue: { type: String, default: '' },
  label: { type: String, default: '命名规则' },
  single: { type: Boolean, default: false },   // true: 单层模式(文件名/NFO), false: 多层层级(目录)
  lengthMax: { type: Number, default: 0 },     // 目录名最大长度, 0 = 不限制
  byByte: { type: Boolean, default: false },   // 按 UTF-8 字节计长
  disabled: { type: Boolean, default: false }, // 整体禁用(置灰 + 禁交互), 如关闭『整理时移动文件』时
})

const emit = defineEmits(['update:modelValue'])

/* ---------- 核心状态 ---------- */
const mode = ref('visual')
const activeLayer = ref(0)
const sourceDraft = ref('')
const layers = ref(props.single ? [tokenizeSegment(props.modelValue)] : parsePattern(props.modelValue))

const serialized = computed(() =>
  props.single ? serializeLayers([layers.value[0] || []]) : serializeLayers(layers.value)
)
const rendered = computed(() => renderPattern(serialized.value))
const unknown = computed(() => findUnknownVars(serialized.value))
const emptyLayer = computed(() => hasEmptyLayer(props.single ? [layers.value[0] || []] : layers.value))
const measured = computed(() => measureLength(rendered.value, props.byByte))
const overLimit = computed(() => props.lengthMax > 0 && measured.value > props.lengthMax)
const meterPct = computed(() => {
  if (!props.lengthMax) return Math.min(100, measured.value > 0 ? 6 : 0)
  return Math.min(100, Math.round((measured.value / props.lengthMax) * 100))
})

/* ---------- 外部值 → 内部结构(避免循环 emit) ---------- */
watch(() => props.modelValue, (v) => {
  if (v === serialized.value) return
  layers.value = props.single ? [tokenizeSegment(v)] : parsePattern(v)
})

function commit() {
  emit('update:modelValue', serialized.value)
}

/* ---------- 变量/符号/文本 添加 ---------- */
function labelOf(key) {
  const v = NAMING_VARS.find((x) => x.key === key)
  return v ? v.label : key
}

function appendVar(key) {
  mutate((draft) => { draft[activeLayer.value].push({ type: 'var', value: key }) })
}

function appendText(text) {
  mutate((draft) => { draft[activeLayer.value].push({ type: 'text', value: text }) })
}

function appendCustomText() {
  const text = window.prompt('输入自定义文本(原样出现在路径中):')
  if (text) appendText(text)
}

/* ---------- 层级操作 ---------- */
function addLayer() {
  mutate((draft) => { draft.splice(activeLayer.value + 1, 0, []) })
  activeLayer.value = Math.min(activeLayer.value + 1, layers.value.length - 1)
}

function removeLayer(li) {
  if (props.single) return
  mutate((draft) => { draft.splice(li, 1) }, `删除第 ${li + 1} 层`)
  activeLayer.value = Math.max(0, Math.min(activeLayer.value, layers.value.length - 1))
}

function moveLayer(li, dir) {
  const target = li + dir
  if (target < 0 || target >= layers.value.length) return
  mutate((draft) => { draft.splice(li, 0, draft.splice(target, 1)[0]) })
  activeLayer.value = target
}

function removeToken(li, ti) {
  mutate((draft) => { draft[li].splice(ti, 1) }, '删除元素')
}

/* ---------- 拖拽(token 层内排序 + 跨层搬运) ---------- */
const dragFrom = ref(null)    // { layer, index }
const dropTarget = ref(null)  // { layer, index, pos: 'before'|'after' } ; index=-1 表示层尾

function onTokenDragStart(li, ti) { dragFrom.value = { layer: li, index: ti } }
function onTokenDragEnd() { dragFrom.value = null; dropTarget.value = null }

function onTokenDragOver(li, ti, e) {
  if (!dragFrom.value) return
  const rect = e.currentTarget.getBoundingClientRect()
  const pos = e.clientX < rect.left + rect.width / 2 ? 'before' : 'after'
  dropTarget.value = { layer: li, index: ti, pos }
}

function onLayerDragOver(li) {
  if (!dragFrom.value) return
  dropTarget.value = { layer: li, index: -1, pos: 'after' }
}

function onTokenDrop(li, ti) {
  if (!dragFrom.value) return
  moveToken(dragFrom.value, dropTarget.value && dropTarget.value.layer === li ? dropTarget.value : { layer: li, index: ti, pos: 'after' })
  dragFrom.value = null
  dropTarget.value = null
}

function onLayerDrop(li) {
  if (!dragFrom.value) return
  moveToken(dragFrom.value, { layer: li, index: -1, pos: 'after' })
  dragFrom.value = null
  dropTarget.value = null
}

function moveToken(from, to) {
  mutate((draft) => {
    if (from.layer >= draft.length || to.layer >= draft.length) return
    const [tk] = draft[from.layer].splice(from.index, 1)
    let target = to.index
    if (to.index === -1) target = draft[to.layer].length       // 层尾
    else {
      if (from.layer === to.layer && from.index < to.index) target -= 1
      if (to.pos === 'after') target += 1
    }
    target = Math.max(0, Math.min(target, draft[to.layer].length))
    draft[to.layer].splice(target, 0, tk)
  }, '移动元素')
}

/* ---------- 源码模式 ---------- */
function switchToSource() {
  sourceDraft.value = serialized.value
  mode.value = 'source'
}

function commitSource() {
  layers.value = props.single ? [tokenizeSegment(sourceDraft.value)] : parsePattern(sourceDraft.value)
  commit()
  mode.value = 'visual'
}

/* ---------- 快速模板 ---------- */
function applyPreset(p) {
  mutate((draft) => {
    const next = parsePattern(p.pattern)
    draft.length = 0
    draft.push(...next)
  }, `应用模板「${p.name}」`)
  mode.value = 'visual'
}

/* ---------- 撤销(5 秒窗口) ---------- */
const undoToast = ref(null)
let undoTimer = null
let countdownTimer = null

function mutate(fn, message) {
  const snapshot = cloneLayers(layers.value)
  const draft = cloneLayers(layers.value)
  fn(draft)
  layers.value = draft
  commit()
  if (message) {
    undoToast.value = { message, snapshot, remain: 5 }
    clearTimeout(undoTimer)
    undoTimer = setTimeout(() => { undoToast.value = null }, 5000)
    clearInterval(countdownTimer)
    countdownTimer = setInterval(() => {
      if (!undoToast.value) { clearInterval(countdownTimer); return }
      undoToast.value.remain -= 1
      if (undoToast.value.remain <= 0) { undoToast.value = null; clearInterval(countdownTimer) }
    }, 1000)
  }
}

function performUndo() {
  if (!undoToast.value) return
  layers.value = undoToast.value.snapshot
  commit()
  undoToast.value = null
  clearTimeout(undoTimer)
  clearInterval(countdownTimer)
}

function cloneLayers(src) {
  return src.map((layer) => layer.map((t) => ({ ...t })))
}

onBeforeUnmount(() => {
  clearTimeout(undoTimer)
  clearInterval(countdownTimer)
})
</script>

<style scoped>
.naming-builder { display: flex; flex-direction: column; gap: var(--space-3); }

/* ---------- 头部 + 分段控件 ---------- */
.builder-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.builder-label { font-size: var(--font-sm); font-weight: 500; color: var(--color-text-secondary); }

.seg {
  display: inline-flex;
  padding: 2px;
  gap: 2px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
}
.seg-btn {
  padding: 3px var(--space-3);
  font-family: inherit;
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--transition-fast);
}
.seg-btn--on {
  color: var(--color-text-primary);
  background: var(--color-bg-surface);
  box-shadow: var(--shadow-sm);
}
.seg-btn:focus-visible { outline: none; box-shadow: var(--focus-ring); }

/* ---------- 三栏主体 ---------- */
.builder-body {
  display: grid;
  grid-template-columns: 208px minmax(0, 1fr) 176px;
  gap: var(--space-3);
  align-items: start;
}
@media (max-width: 1100px) {
  .builder-body { grid-template-columns: 1fr; }
}

/* 左栏: 变量面板 */
.var-panel {
  padding: var(--space-3);
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
}
.var-panel-tip { font-size: var(--font-xs); color: var(--color-text-tertiary); margin-bottom: var(--space-2); }
.var-panel-tip b { color: var(--color-primary); font-weight: 500; }

.var-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: var(--space-2); }

.var-chip {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 1px;
  padding: var(--space-1) var(--space-2);
  font-family: inherit;
  text-align: left;
  background: var(--color-primary-soft);
  border: 0;
  border-left: 3px solid var(--color-primary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: filter var(--transition-fast);
}
.var-chip:hover { filter: brightness(0.96); }
.var-chip:active { transform: translateY(1px); }
.var-chip:focus-visible { outline: none; box-shadow: var(--focus-ring); }
.var-chip-label { font-size: var(--font-sm); font-weight: 500; color: var(--color-primary); }
.var-chip-key { font-size: 9px; font-family: var(--font-mono); color: var(--color-primary); opacity: 0.72; }

.var-chip--custom {
  background: var(--color-bg-subtle);
  border: 1px dashed var(--color-border-medium);
  border-left: 3px solid var(--color-border-medium);
  justify-content: center;
}
.var-chip--custom .var-chip-label { color: var(--color-text-secondary); font-weight: 400; }

.symbol-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-1);
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border-light);
}
.symbol-row-label { width: 100%; font-size: var(--font-xs); color: var(--color-text-tertiary); }
.symbol-btn {
  min-width: 24px;
  min-height: 24px;
  font-family: var(--font-mono);
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.symbol-btn:hover { background: var(--color-bg-hover); color: var(--color-text-primary); }
.symbol-btn:focus-visible { outline: none; box-shadow: var(--focus-ring); }

/* 中栏: 层级树 */
.layer-tree {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
}
.layer-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
  cursor: default;
  transition: border-color var(--transition-fast), background var(--transition-fast);
}
.layer-row--active { border-color: var(--color-primary); background: var(--color-primary-soft); }
.layer-row--dragover { border-color: var(--color-primary); border-style: dashed; }

.layer-index {
  flex-shrink: 0;
  width: 28px;
  font-size: var(--font-xs);
  font-weight: 500;
  font-family: var(--font-mono);
  color: var(--color-text-tertiary);
  text-align: center;
}
.layer-row--active .layer-index { color: var(--color-primary); }

.token-flow {
  flex: 1;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-1);
  min-width: 0;
  min-height: 28px;
}

.token-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 2px 2px var(--space-2);
  font-size: var(--font-sm);
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border-light);
  cursor: grab;
  user-select: none;
}
.token-chip:active { cursor: grabbing; }
.token--var {
  background: var(--color-primary-soft);
  border-left: 3px solid var(--color-primary);
  color: var(--color-primary);
  font-weight: 500;
}
.token--var.token--unknown {
  background: var(--color-danger-soft);
  border-left-color: var(--color-danger);
  color: var(--color-danger);
}
.token--text { background: var(--color-bg-subtle); color: var(--color-text-secondary); font-family: var(--font-mono); }
.token--dragging { opacity: 0.45; }
.token--drop-before { box-shadow: -2px 0 0 0 var(--color-primary); }
.token--drop-after { box-shadow: 2px 0 0 0 var(--color-primary); }

.token-del {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  padding: 0;
  font-size: 9px;
  color: var(--color-text-tertiary);
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.token-del:hover { color: var(--color-danger); background: var(--color-danger-soft); }

.token-empty { font-size: var(--font-xs); color: var(--color-text-tertiary); }

.layer-ops { display: flex; gap: 2px; flex-shrink: 0; }
.layer-op {
  width: 22px;
  height: 22px;
  font-size: var(--font-xs);
  color: var(--color-text-tertiary);
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.layer-op:hover:not(:disabled) { color: var(--color-text-primary); background: var(--color-bg-hover); }
.layer-op:disabled { opacity: 0.3; cursor: not-allowed; }
.layer-op--del:hover:not(:disabled) { color: var(--color-danger); background: var(--color-danger-soft); }
.layer-op:focus-visible { outline: none; box-shadow: var(--focus-ring); }

/* 整体禁用态: 关闭『整理时移动文件』时由父级传入 disabled 触发 */
.naming-builder--disabled { opacity: 0.45; pointer-events: none; filter: grayscale(1); cursor: not-allowed; }

.layer-add {
  align-self: flex-start;
  padding: var(--space-1) var(--space-3);
  font-family: inherit;
  font-size: var(--font-sm);
  color: var(--color-primary);
  background: transparent;
  border: 1px dashed var(--color-primary);
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.layer-add:hover { background: var(--color-primary-soft); }
.layer-hint { font-size: var(--font-xs); color: var(--color-text-tertiary); }
.layer-hint code { font-family: var(--font-mono); }

/* 右栏: 预览 */
.builder-preview {
  padding: var(--space-3);
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
}
.preview-title { font-size: var(--font-xs); font-weight: 500; color: var(--color-text-secondary); margin-bottom: var(--space-2); }
.preview-path {
  font-family: var(--font-mono);
  font-size: var(--font-sm);
  color: var(--color-text-primary);
  word-break: break-all;
  line-height: 1.5;
  min-height: 2.4em;
}
.preview-meter {
  height: 4px;
  margin-top: var(--space-2);
  background: var(--color-bg-hover);
  border-radius: var(--radius-full);
  overflow: hidden;
}
.preview-meter-bar { height: 100%; background: var(--color-primary); border-radius: var(--radius-full); transition: width var(--transition-base); }
.preview-meter--over .preview-meter-bar { background: var(--color-danger); }
.preview-length { margin-top: var(--space-1); font-size: var(--font-xs); color: var(--color-text-tertiary); font-variant-numeric: tabular-nums; }
.preview-length--over { color: var(--color-danger); }

.preview-alert { margin-top: var(--space-2); font-size: var(--font-xs); line-height: 1.5; }
.preview-alert--ok { color: var(--color-success); }
.preview-alert--warning { color: var(--color-warning); }
.preview-alert--danger { color: var(--color-danger); }

/* ---------- 源码模式 ---------- */
.source-body { display: flex; flex-direction: column; gap: var(--space-2); }
.source-input {
  width: 100%;
  padding: var(--space-3);
  font-family: var(--font-mono);
  font-size: var(--font-base);
  color: var(--color-text-primary);
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-medium);
  border-radius: var(--radius-md);
  resize: vertical;
}
.source-input:focus { outline: none; border-color: var(--color-primary); box-shadow: var(--focus-ring); }
.source-foot { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.source-tip { font-size: var(--font-xs); color: var(--color-text-tertiary); }

/* ---------- 快速模板 ---------- */
.preset-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.preset-label { font-size: var(--font-xs); color: var(--color-text-tertiary); }
.preset-btn {
  padding: 3px var(--space-3);
  font-family: inherit;
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-full);
  cursor: pointer;
}
.preset-btn:hover { color: var(--color-primary); border-color: var(--color-primary); }
.preset-btn:focus-visible { outline: none; box-shadow: var(--focus-ring); }

/* ---------- 撤销 toast ---------- */
.undo-toast {
  position: fixed;
  left: 50%;
  bottom: calc(var(--statusbar-height) + var(--space-4));
  transform: translateX(-50%);
  z-index: var(--z-toast);
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-sm);
  color: var(--color-text-primary);
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-medium);
  border-radius: var(--radius-full);
  box-shadow: var(--shadow-toast);
}
.undo-btn {
  padding: 2px var(--space-3);
  font-family: inherit;
  font-size: var(--font-sm);
  font-weight: 500;
  color: var(--color-primary);
  background: var(--color-primary-soft);
  border: 0;
  border-radius: var(--radius-full);
  cursor: pointer;
}
.undo-count { font-size: var(--font-xs); color: var(--color-text-tertiary); font-variant-numeric: tabular-nums; }

.undo-toast-enter-active, .undo-toast-leave-active { transition: opacity var(--transition-base), transform var(--transition-base); }
.undo-toast-enter-from, .undo-toast-leave-to { opacity: 0; transform: translateX(-50%) translateY(8px); }
</style>
