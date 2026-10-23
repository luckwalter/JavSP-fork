import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时把 /api 代理到本地 FastAPI 服务(默认 8000 端口)
// 生产构建产物(dist)由后端 javsp/server.py 直接托管
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000'
    }
  },
  build: {
    outDir: 'dist',
    // 下调产物语法基线: Win10 上 WebView2 Runtime 可能长期停留在旧版(Chromium 8x),
    // 用 es2017 而非默认的 es2020/modules, 可覆盖更旧的内核(Chrome 60+)
    target: 'es2017'
  }
})
