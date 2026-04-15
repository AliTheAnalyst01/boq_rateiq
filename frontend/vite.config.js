import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') }
  },
  server: {
    port: 3000,
    proxy: {
      '/fill-boq': {
        target: 'http://localhost:8000',
        changeOrigin: true
      },
      '/rate-item': {
        target: 'http://localhost:8000',
        changeOrigin: true
      },
      '/rate-batch': {
        target: 'http://localhost:8000',
        changeOrigin: true
      },
      '/health': {
        target: 'http://localhost:8000',
        changeOrigin: true
      },
      '/search': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  }
})
