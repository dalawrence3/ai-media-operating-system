import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': new URL('./src', import.meta.url).pathname,
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        // Phase 18E: the backend origin is configurable so the E2E frontend can
        // be pointed at the isolated test backend. Hardcoding :8000 meant an
        // E2E frontend proxied straight into the live Media OS regardless of
        // which backend Playwright had started.
        target: process.env.ACE_BACKEND_URL || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Exclude Playwright e2e tests — run those with 'make e2e' instead.
    exclude: ['node_modules/**', 'dist/**', 'e2e/**', '**/*.spec.ts', '**/*.global-setup.ts'],
    environmentOptions: {
      jsdom: { url: 'http://localhost:5173' },
    },
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      exclude: ['node_modules/', 'dist/', 'src/test/', 'e2e/'],
    },
  },
})
