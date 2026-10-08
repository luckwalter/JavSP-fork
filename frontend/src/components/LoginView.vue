<script setup>
/** 登录页
 *  三态设计: 默认 / 错误(剩余次数) / 锁定(倒计时)。
 *  安全说明: 本页只是呈现层 —— 真正的校验、计数与锁定都在后端 auth.py,
 *  这里拿到的 remaining 来自服务端, 前端无法自行解除锁定。
 */
import { ref, computed, onUnmounted } from 'vue'

const props = defineProps({
  username: { type: String, default: 'admin' },
  lockedFor: { type: Number, default: 0 },
  // 非错误类提示(如"会话已失效, 请重新登录")。与 error 区分: 它是**预期内**的状态,
  // 不该把输入框标红吓人一跳 —— 用户只是被登出了, 照常登录即可。
  notice: { type: String, default: '' },
})
const emit = defineEmits(['login', 'forgot'])

const user = ref(props.username || 'admin')
const pass = ref('')
const remember = ref(true)
const submitting = ref(false)
const error = ref('')
const lockRemain = ref(props.lockedFor || 0)

let timer = null

// 锁定倒计时: 服务端给的是初始值, 之后由前端本地递减显示(最终仍以服务端为准)
if (lockRemain.value > 0) startCountdown()

function startCountdown() {
  if (timer) clearInterval(timer)
  timer = setInterval(() => {
    lockRemain.value = Math.max(0, lockRemain.value - 1)
    if (lockRemain.value <= 0 && timer) {
      clearInterval(timer)
      timer = null
      error.value = ''
    }
  }, 1000)
}

onUnmounted(() => { if (timer) clearInterval(timer) })

const locked = computed(() => lockRemain.value > 0)
const mm = computed(() => String(Math.floor(lockRemain.value / 60)).padStart(2, '0'))
const ss = computed(() => String(lockRemain.value % 60).padStart(2, '0'))

async function submit() {
  if (submitting.value || locked.value) return
  if (!user.value.trim()) { error.value = '请输入用户名'; return }
  if (!pass.value) { error.value = '请输入密码'; return }
  submitting.value = true
  error.value = ''
  try {
    await emit('login', {
      username: user.value.trim(),
      password: pass.value,
      remember: remember.value,
    })
    // emit 是异步的, 失败信息由父组件通过 lockedFor / error 回传
  } finally {
    submitting.value = false
  }
}

defineExpose({ setError: (m, lock = 0) => { error.value = m; if (lock > 0) { lockRemain.value = lock; startCountdown() } } })
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-brand">
        <div class="login-logo" aria-hidden="true">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <rect x="4" y="10" width="16" height="10" rx="2" />
            <path d="M8 10V7a4 4 0 0 1 8 0v3" />
          </svg>
        </div>
        <h1 class="login-title">JavSP</h1>
        <p class="login-sub">媒体库刮削管理台</p>
      </div>

      <!-- 锁定态: 优先级最高, 屏蔽一切输入, 避免无效尝试 -->
      <template v-if="locked">
        <div class="login-locked">
          <p class="login-locked-title">尝试次数过多</p>
          <p class="login-locked-sub">已临时锁定，请稍后再试</p>
          <p class="login-countdown" role="timer" aria-live="polite">{{ mm }}:{{ ss }}</p>
        </div>
        <button class="btn btn--block" disabled>登录</button>
      </template>

      <template v-else>
        <div v-if="notice && !error" class="notice notice--info login-alert" role="status">
          <span>{{ notice }}</span>
        </div>

        <div v-if="error" class="notice notice--danger login-alert" role="alert">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="1.8" stroke-linecap="round" aria-hidden="true"
               style="flex-shrink: 0; margin-top: 2px;">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8v5M12 16h.01" />
          </svg>
          <span>{{ error }}</span>
        </div>

        <form @submit.prevent="submit">
          <div class="field">
            <label class="field-label" for="login-user">用户名</label>
            <input
              id="login-user"
              v-model="user"
              class="input"
              :class="{ 'input--error': !!error }"
              autocomplete="username"
              :disabled="submitting"
            />
          </div>

          <div class="field">
            <label class="field-label" for="login-pass">密码</label>
            <input
              id="login-pass"
              v-model="pass"
              type="password"
              class="input"
              :class="{ 'input--error': !!error }"
              autocomplete="current-password"
              :disabled="submitting"
            />
          </div>

          <div class="login-row">
            <label class="login-check">
              <input v-model="remember" type="checkbox" :disabled="submitting" />
              <span>记住此设备</span>
            </label>
            <button type="button" class="link-btn" @click="emit('forgot')">忘记密码</button>
          </div>

          <button type="submit" class="btn btn--primary btn--block" :disabled="submitting">
            <span v-if="submitting" class="spinner" aria-hidden="true"></span>
            {{ submitting ? '登录中' : '登录' }}
          </button>
        </form>

        <p class="login-note">
          初始账号密码通过服务器环境变量设置<br />
          (<code>JAVSP_AUTH_PASSWORD</code>)
        </p>

        <!-- 初始密码提示: 便于自部署用户首次登录。
             ⚠️ 安全代价: 公网暴露时等于把凭据写在页面上。适用前提是纯内网自用;
             一旦放到公网, 请删掉这一行(以及 .env 的初始密码)。 -->
        <p class="login-hint">
          初始账号 <code>admin</code> · 初始密码 <code>admin</code>
          <span class="login-hint-warn">（仅内网自用；公网部署请删除此提示并改密码）</span>
        </p>
      </template>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: var(--space-6);
  background: var(--color-bg-page);
}

.login-card {
  width: 100%;
  max-width: 340px;
  padding: var(--space-8) var(--space-6);
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
}

.login-brand {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: var(--space-6);
}

.login-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  margin-bottom: var(--space-3);
  color: var(--color-primary);
  background: var(--color-primary-soft);
  border-radius: var(--radius-md);
}

.login-title { font-size: var(--font-xl); font-weight: 600; }
.login-sub { margin-top: 2px; font-size: var(--font-sm); color: var(--color-text-secondary); }

.login-alert { margin-bottom: var(--space-4); }

.login-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-5);
}

.login-check {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
  cursor: pointer;
}

.link-btn {
  padding: 0;
  font-family: inherit;
  font-size: var(--font-sm);
  color: var(--color-primary);
  background: none;
  border: 0;
  cursor: pointer;
}

.link-btn:hover { text-decoration: underline; }
.link-btn:focus-visible { outline: none; box-shadow: var(--focus-ring); border-radius: var(--radius-sm); }

.login-note {
  margin-top: var(--space-4);
  font-size: var(--font-xs);
  color: var(--color-text-tertiary);
  text-align: center;
}

/* 初始密码提示 */
.login-hint {
  margin-top: var(--space-3);
  padding: var(--space-2) var(--space-3);
  font-size: var(--font-xs);
  color: #7a4d06;
  text-align: center;
  background: var(--color-warning-soft);
  border-radius: var(--radius-sm);
}

.login-hint-warn {
  display: block;
  margin-top: 2px;
  color: #8a6d3b;
}

.login-hint code {
  padding: 1px 4px;
  font-family: var(--font-mono);
  background: rgba(255, 255, 255, 0.7);
  border-radius: 3px;
}

/* 锁定态 */
.login-locked { margin-bottom: var(--space-5); text-align: center; }
.login-locked-title { font-size: var(--font-md); font-weight: 600; color: var(--color-text-danger); }
.login-locked-sub { margin-top: var(--space-1); font-size: var(--font-base); color: var(--color-text-secondary); }
.login-countdown {
  margin-top: var(--space-4);
  font-size: var(--font-2xl);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
</style>
