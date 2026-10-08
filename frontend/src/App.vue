<script setup>
/**
 * JavSP WebUI 应用骨架
 * ---------------------------------------------------------------------------
 * 职责划分:
 *   App.vue            —— 只做「认证门禁 + 导航 + 状态栏」三件事, 不含业务逻辑
 *   components/        —— 登录页/ 导航栏 / 状态栏 / 确认框等通用件
 *   views/             —— 各页面业务(见下)
 *   styles/tokens.css  —— 设计令牌单一数据源
 *
 * 相比旧版的改动:
 *   1. 登录门禁 —— 未登录不渲染任何业务界面, 避免"界面可见但接口 401"的半残状态
 *   2. 导航重构 —— 主工作流按使用顺序纵向排列, 工具类下沉, 破坏性操作独立分组
 *   3. 状态常驻 —— 渠道健康/任务进度放底部状态栏, 不用切页面查
 *   4. 破坏性操作二次确认 —— 默认焦点在「取消」
 */
import { ref, computed, onMounted, onUnmounted, provide, inject } from 'vue'

import NavRail from './components/NavRail.vue'
import StatusBar from './components/StatusBar.vue'
import LoginView from './components/LoginView.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'

import LibraryView from './views/LibraryView.vue'
import QueueView from './views/QueueView.vue'
import ReviewView from './views/ReviewView.vue'
import LookupView from './views/LookupView.vue'
import OrganizeView from './views/OrganizeView.vue'
import ChannelsView from './views/ChannelsView.vue'
import SettingsView from './views/SettingsView.vue'

import * as api from './api'

// ---------------- 认证状态 ----------------
const authReady = ref(false)     // 启动时先问后端要不要登录, 避免"闪一下登录页"
const authEnabled = ref(false)   // 后端是否启用了认证(未启用则完全跳过登录)
const loggedIn = ref(false)
const loginRef = ref(null)
const userName = ref('')

const version = ref('')
const active = ref('library')   // 导航项(与 NavRail 的 key 对应)

// ---------------- 渠道健康(供状态栏与导航角标) ----------------
const health = ref({ active: 0, ok: 0, tripped: 0 })

async function refreshHealth(silent = false) {
  try {
    const d = await api.getChannels()
    health.value = d.summary || {}
  } catch (_) {
    // 后端不可达或未登录时静默: 状态栏保持空态即可。
    // silent=true 用于启动时的会话探测 —— 那次调用本就预期可能 401,
    // 若让它触发 unauthorized 广播, 会把用户踢回登录页。
    if (!silent) health.value = {}
  }
}

// ---------------- 破坏性操作确认 ----------------
// 用 provide/inject 而非 emit: `emit` 是同步的, 不返回 Promise ——
// 子组件 `await emit('ask-confirm')` 只会拿到 undefined(恒 falsy),
// 表现为"确认对话框点确定也没反应"。需要返回值就必须走 provide。
// 字符串 key 而非 Symbol: Symbol 需从同一模块导出才能被子组件 import 到,
// 字符串 key 无此约束, 也不会因打包作用域问题导致 inject 失败。
const CONFIRM_KEY = 'javsp:confirm'
const confirmState = ref({
  show: false, title: '', impact: '', detail: '', confirmText: '确认', busy: false,
})
let confirmResolver = null

function askConfirm(opts) {
  return new Promise((resolve) => {
    confirmResolver = resolve
    confirmState.value = {
      show: true,
      busy: false,
      title: opts.title || '确认执行',
      impact: opts.impact || '',
      detail: opts.detail || '',
      confirmText: opts.confirmText || '确认',
    }
  })
}

provide(CONFIRM_KEY, askConfirm)

function onConfirmDone() {
  confirmState.value = { ...confirmState.value, show: false, busy: false }
  const r = confirmResolver
  confirmResolver = null
  if (r) r(true)
}

function onConfirmCancel() {
  confirmState.value = { ...confirmState.value, show: false }
  const r = confirmResolver
  confirmResolver = null
  if (r) r(false)
}

// ---------------- 导航 ----------------
const VIEWS = {
  library: LibraryView,
  queue: QueueView,
  review: ReviewView,
  lookup: LookupView,
  organize: OrganizeView,
  channels: ChannelsView,
  settings: SettingsView,
}
const currentView = computed(() => VIEWS[active.value] || LibraryView)

// 从子页面冒泡上来的事件
function onNavigate(key) {
  active.value = key
  if (key === 'channels') refreshHealth()
}

// ---------------- 登录流程 ----------------
async function doLogin({ username, password, remember }) {
  try {
    await api.login(username, password, remember)
    loggedIn.value = true
    sessionNotice.value = ''
    userName.value = username
    await afterLogin()
  } catch (e) {
    loginRef.value?.setError(e.message, e.lockedFor || 0)
  }
}

async function doLogout() {
  try {
    await api.logout()
  } catch (_) {
    // 即使注销请求失败也切回登录页, 避免卡在无权限状态
  }
  loggedIn.value = false
}

// 浏览根(供影片库默认目录) —— 必须先于 onMounted 使用 loadBrowseRoot 声明
const browseRoot = ref('')

/** 登录成功后拉取浏览根(供影片库预填), 失败不阻断 */
async function loadBrowseRoot() {
  try {
    const d = await api.browse('')
    browseRoot.value = d.root || ''
  } catch (_) {
    /* 忽略 */
  }
}

async function afterLogin() {
  try {
    const h = await api.getHealth()
    version.value = h.version || ''
  } catch (_) {
    // 版本号拿不到不影响主流程
  }
  await refreshHealth()
  await loadBrowseRoot()
}

// 全局 401: 由 api.request 广播 —— 后端会话过期时立刻切登录页
// 🔑 这里必须"真的切走当前页面": 早期版本里只有部分接口走 request(), 其余裸 fetch
// 拿到 401 后只是把错误文本塞进页面, App 完全不知情 —— 用户看到红字却没有任何出口,
// 点保存/重试都原地打转(即反馈里的「点了没反应」)。现在所有接口统一广播, 一旦会话
// 失效就整体退回登录页并说明原因。
const sessionNotice = ref('')
let onUnauthorized = null
function handleUnauthorized() {
  if (loggedIn.value) sessionNotice.value = '会话已失效，请重新登录'
  loggedIn.value = false
}

onMounted(async () => {
  onUnauthorized = () => handleUnauthorized()
  api.authEvents.addEventListener('unauthorized', onUnauthorized)

  try {
    const s = await api.getAuthStatus()
    authEnabled.value = !!s.enabled
    authReady.value = true
    if (!s.enabled) {
      // 后端未启用认证 -> 直接进入应用, 前端不显示登录页
      loggedIn.value = true
      await afterLogin()
    } else {
      // 🔑 启用认证时必须**主动验证会话是否还有效**, 不能停在登录页。
      // 否则刷新页面后即使用户 cookie 仍然有效, 也会被要求重新登录 ——
      // 表现为"操作一会儿就退出登录", 实则是刷新即登出(v0.2.2 上线后的实测反馈)。
      // /api/channels 需要登录; 401 会由 request() 广播 unauthorized, 这里只需
      // 捕获异常避免未处理的 promise rejection。
      try {
        await api.getChannels()
        loggedIn.value = true
        userName.value = s.username || ''
        await afterLogin()
      } catch (_) {
        loggedIn.value = false      // 会话确实没了 -> 显示登录页
      }
    }
  } catch (_) {
    // 后端不可达: 仍展示登录页(但提示), 而不是白屏
    authReady.value = true
    authEnabled.value = true
  }
})

onUnmounted(() => {
  if (onUnauthorized) api.authEvents.removeEventListener('unauthorized', onUnauthorized)
})
</script>

<template>
  <!-- 启动探测中: 不闪烁登录页 -->
  <div v-if="!authReady" class="boot">
    <div class="spinner" aria-label="加载中"></div>
  </div>

  <!-- 需要登录且未登录 -->
  <LoginView
    v-else-if="authEnabled && !loggedIn"
    ref="loginRef"
    :locked-for="0"
    :notice="sessionNotice"
    @login="doLogin"
  />

  <!-- 主应用 -->
  <div v-else class="app-shell">
    <NavRail
      :active="active"
      :trip-count="health.tripped || 0"
      @navigate="onNavigate"
    />

    <div class="app-main">
      <main class="app-content">
        <div class="content-inner">
          <!-- 动态组件承载当前页面; 状态经 sessionStorage 保持, 刷新可接续 -->
          <component
            :is="currentView"
            :key="active"
            :version="version"
            :health="health"
            :browse-root="browseRoot"
            @navigate="onNavigate"
            @refresh-health="refreshHealth"
          />
        </div>
      </main>

      <StatusBar
        :version="version"
        :health="health"
        :user="authEnabled ? userName : ''"
        @logout="doLogout"
        @navigate="onNavigate"
      />
    </div>
  </div>

  <ConfirmDialog
    :show="confirmState.show"
    :title="confirmState.title"
    :impact="confirmState.impact"
    :detail="confirmState.detail"
    :confirm-text="confirmState.confirmText"
    :busy="confirmState.busy"
    @confirm="onConfirmDone"
    @cancel="onConfirmCancel"
  />
</template>

<style scoped>
.boot {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
}
</style>
