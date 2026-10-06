import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// Dev server: proxies /api and /health to the FastAPI backend so the browser stays same-origin.
const API = process.env.API_BASE || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    host: process.env.HOST || '127.0.0.1',
    port: Number(process.env.PORT || 5173),
    strictPort: true,
    proxy: {
      '/api': { target: API, changeOrigin: true },
      '/health': { target: API, changeOrigin: true },
    },
  },
  build: {
    chunkSizeWarningLimit: 1600,
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'],
          'element-plus': ['element-plus'],
        },
      },
    },
  },
})
