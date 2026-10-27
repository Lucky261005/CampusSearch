/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  // backend for the dev/preview proxy; the browser only ever calls same-origin /api
  const target = loadEnv(mode, '.', '').API_PROXY_TARGET || 'http://127.0.0.1:8000'
  const proxy = { '/api': { target, changeOrigin: true } }
  return {
    plugins: [react(), tailwindcss()],
    server: { proxy },
    preview: { proxy },
    test: {
      environment: 'jsdom',
      setupFiles: './src/setupTests.ts',
    },
  }
})
