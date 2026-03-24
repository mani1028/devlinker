import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
const oneLinkMode = process.env.ONELINK === '1'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    // Keep HMR for local dev; disable when OneLink launches frontend.
    hmr: oneLinkMode ? false : true,
  },
})
