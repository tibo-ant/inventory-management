import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // Listen on all interfaces, not just the loopback Vite defaults to.
    // Without this, `localhost` can resolve to only [::1], so anything that
    // dials 127.0.0.1:3000 (port forwarders, remote dev containers) gets
    // connection refused even though the server is up. The FastAPI backend
    // already binds 0.0.0.0 for the same reason.
    host: true,
    port: 3000
  }
})
