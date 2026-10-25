<script setup>
/** 全局更新提示条(v0.4.0)
 *
 * 行为:
 * - 挂载时向后端要一次检查结果(后端启动时已后台查过并缓存, 这里几乎瞬时返回);
 *   网络不可达时后端返回 available=false, 界面不出现任何东西 —— 绝不因为
 *   查不到更新就打扰用户。
 * - 发现新版本才显示。Windows 桌面版可一键升级(下载 → helper 接管覆盖 → 重启);
 *   Docker 部署无法替换自身镜像, 只给升级命令与发布页。
 * - "不再提示此版本"按版本号记在 localStorage, 同一版本不再打扰。
 */
import { ref, computed, onMounted } from 'vue'
import * as api from '../api'

const SKIP_KEY = 'javsp:skipUpdateVersion'

const info = ref(null)          // 后端返回的更新信息
const visible = ref(false)
const busy = ref(false)         // 下载/应用中
const progress = ref(null)      // {downloaded, total}
const errorMsg = ref('')
const copied = ref(false)

const current = computed(() => (info.value && info.value.current) || '')
const latest = computed(() => (info.value && info.value.latest) || '')
const isWindows = computed(() => !!(info.value && info.value.frozen))
const isContainer = computed(() => !!(info.value && info.value.container))
const dockerHint = computed(() => (info.value && info.value.docker_hint) || '')

const percent = computed(() => {
  const p = progress.value
  if (!p || !p.total) return 0
  return Math.min(100, Math.round((p.downloaded / p.total) * 100))
})

function skipped(version) {
  try { return localStorage.getItem(SKIP_KEY) === version } catch { return false }
}

function setSkipped(version) {
  try { localStorage.setItem(SKIP_KEY, version) } catch { /* 忽略存储异常 */ }
}

async function check(force = false) {
  try {
    const res = await api.checkUpdate(force)
    info.value = res
    if (res.available && res.info && !skipped(res.info.latest)) {
      visible.value = true
    } else if (!res.available) {
      visible.value = false
    }
    return res
  } catch (e) {
    // 检查失败(网络不通等)一律静默: 不弹错误, 不打扰
    return null
  }
}

function dismiss() {
  visible.value = false
}

function neverShowThisVersion() {
  if (latest.value) setSkipped(latest.value)
  visible.value = false
}

function openRelease() {
  if (info.value && info.value.info && info.value.info.url) {
    window.open(info.value.info.url, '_blank', 'noopener')
  }
}

async function copyDockerHint() {
  const text = dockerHint.value
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch {
    // 剪贴板不可用(如非安全上下文)时退回到显示命令文本, 供手动复制
    window.prompt('请手动复制以下升级命令:', text)
  }
}

async function doUpgrade() {
  if (busy.value) return
  busy.value = true
  errorMsg.value = ''
  progress.value = null
  try {
    let zipPath = ''
    await api.downloadUpdate((evt) => {
      if (evt.type === 'progress') {
        progress.value = { downloaded: evt.downloaded, total: evt.total }
      } else if (evt.type === 'result') {
        zipPath = evt.path
      } else if (evt.type === 'error') {
        errorMsg.value = evt.msg || '下载失败'
      }
    })
    if (!zipPath) {
      if (!errorMsg.value) errorMsg.value = '下载未完成, 请重试'
      return
    }
    await api.applyUpdate(zipPath)
    // 后端随即退出主进程, 由 helper 覆盖并重启新版本
    busy.value = false
    errorMsg.value = ''
  } catch (e) {
    errorMsg.value = e.message || String(e)
  } finally {
    busy.value = false
  }
}

onMounted(() => { check(false) })

defineExpose({ check })
</script>

<template>
  <div v-if="visible && info && info.available" class="notice notice--info update-banner"
       style="margin-bottom: var(--space-4);">
    <div class="update-banner__main">
      <strong>发现新版本 v{{ latest }}</strong>
      <span class="dim">（当前 v{{ current }}）</span>
      <p style="margin: 0.35em 0 0; line-height: 1.6;">
        <template v-if="isWindows">下载并自动完成升级（程序会自动重启）。</template>
        <template v-else-if="isContainer">容器内无法替换自身镜像，请按下方命令更新镜像后重启。</template>
        <template v-else>当前为源码/服务方式部署，请到发布页下载对应版本。</template>
      </p>
      <p v-if="errorMsg" style="margin: 0.4em 0 0; color: var(--color-danger);">{{ errorMsg }}</p>
      <div v-if="busy && progress" style="margin-top: 0.5em; max-width: 420px;">
        <div class="update-banner__bar">
          <div class="update-banner__fill" :style="{ width: percent + '%' }"></div>
        </div>
        <p class="dim" style="margin: 0.3em 0 0;">
          正在下载更新包… {{ percent }}%
          <template v-if="progress.total">
            （{{ (progress.downloaded / 1048576).toFixed(1) }} / {{ (progress.total / 1048576).toFixed(1) }} MB）
          </template>
        </p>
      </div>
    </div>
    <div class="update-banner__actions">
      <button v-if="isWindows" class="btn btn--sm" :disabled="busy" @click="doUpgrade">
        {{ busy ? '处理中…' : '立即升级' }}
      </button>
      <button v-else-if="isContainer" class="btn btn--sm" @click="copyDockerHint">
        {{ copied ? '已复制' : '复制升级命令' }}
      </button>
      <button class="btn btn--sm btn--ghost" @click="openRelease">发布说明</button>
      <button class="btn btn--sm btn--ghost" @click="dismiss">稍后</button>
      <button class="btn btn--sm btn--ghost" @click="neverShowThisVersion">不再提示此版本</button>
    </div>
    <p v-if="isContainer && dockerHint" class="update-banner__cmd"><code>{{ dockerHint }}</code></p>
  </div>
</template>

<style scoped>
.update-banner {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: var(--space-3);
}
.update-banner__main { flex: 1; min-width: 280px; }
.update-banner__actions { display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; }
.update-banner__bar {
  height: 6px;
  border-radius: 3px;
  background: var(--color-border-light);
  overflow: hidden;
}
.update-banner__fill {
  height: 100%;
  background: var(--color-primary, #4a8cff);
  transition: width 0.2s ease;
}
.update-banner__cmd {
  flex-basis: 100%;
  margin: 0.2em 0 0;
  font-size: var(--font-sm);
  word-break: break-all;
}
</style>
