import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `npm run dev` serves on 0.0.0.0:5173 so your phone's browser can reach it too.
// /api/* is forwarded to the collector on this machine, so the page can use
// VITE_API_URL=/api when opened through a domain/tunnel (HTTPS, no port 8020).
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8020',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
