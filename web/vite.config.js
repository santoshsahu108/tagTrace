import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `npm run dev` serves on 0.0.0.0:5173 so your phone's browser can reach it too.
export default defineConfig({
  plugins: [react()],
  server: { host: true, port: 5173 },
})
