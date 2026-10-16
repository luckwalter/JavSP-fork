import { createApp } from 'vue'
import App from './App.vue'

// 设计令牌与全局基线。**必须在组件样式之前引入** —— 令牌用 :root 定义,
// 组件里的 var() 才能取到值。
import './styles/tokens.css'
import './styles/base.css'

// 新界面用自建设计系统, 不再引入 Element Plus(体积大且默认样式会与设计令牌冲突)。
// 若后续某个组件确实需要 el-* 组件, 在此处按需引入即可, 不必整体挂载。

// 挂载前先应用主题: 否则刷新页面会白底/深底闪一帧(防闪烁)。
// useTheme 是模块级单例, 这里调用一次即完成读取持久化值 + 写根节点 data-theme。
import { useTheme } from './composables/useTheme'
useTheme()

createApp(App).mount('#app')
