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
    port: 3000,
    // The client calls the API with relative /api URLs (see src/api.js), so
    // the dev server proxies them to the FastAPI backend. Keeping the
    // browser on a single origin means one forwarded port carries the whole
    // app in remote/container setups, instead of also needing 8001.
    // 127.0.0.1 (not `localhost`) so Node cannot resolve to the IPv6
    // loopback, which uvicorn's 0.0.0.0 bind does not cover.
    proxy: {
      '/api': 'http://127.0.0.1:8001'
    }
  }
})
