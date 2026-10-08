import { createApp } from 'vue'
import App from './App.vue'

// 设计令牌与全局基线。**必须在组件样式之前引入** —— 令牌用 :root 定义,
// 组件里的 var() 才能取到值。
import './styles/tokens.css'
import './styles/base.css'

// 新界面用自建设计系统, 不再引入 Element Plus(体积大且默认样式会与设计令牌冲突)。
// 若后续某个组件确实需要 el-* 组件, 在此处按需引入即可, 不必整体挂载。
createApp(App).mount('#app')
