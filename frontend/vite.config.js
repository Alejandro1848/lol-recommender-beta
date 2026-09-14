import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// En dev, el frontend corre en :5173 y proxya /api al backend FastAPI.
// En prod, `npm run build` genera dist/ y FastAPI lo sirve directamente.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
  },
})
