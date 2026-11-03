import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 开发时 /api 请求代理到本地 FastAPI，前端代码里统一用相对路径
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
