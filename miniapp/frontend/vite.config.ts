import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  // Mini App раздаётся из /app/ (StaticFiles mount в main.py).
  // Без base: '/app/' Vite генерирует абсолютные пути /assets/...,
  // а браузер запрашивает их от корня домена — FastAPI отдаёт 404.
  // С base: '/app/' все ссылки станут /app/assets/... и /app/favicon.svg.
  base: '/app/',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    host: true, // 0.0.0.0 — нужно для доступа с телефона в локальной сети
    // Проксируем API-запросы на backend (избегаем CORS в dev)
    // main.py по умолчанию слушает PORT=8080
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/bot': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // Telegram Mini App загружается в WebView — критичен размер бандла
    chunkSizeWarningLimit: 500,
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom'],
          'query-vendor': ['@tanstack/react-query'],
        },
      },
    },
  },
})
