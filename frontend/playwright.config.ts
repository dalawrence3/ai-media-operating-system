import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright E2E configuration.
 *
 * Phase 18E — the E2E runtime is structurally separated from the live Media OS.
 *
 * This file previously started the backend via scripts/start-backend.sh on the
 * SAME port the live system uses, with `reuseExistingServer` on and no
 * ACE_DB_PATH — so on a machine running the live stack, the suite adopted the
 * live backend and drove the live database. A Playwright test revoked the real
 * channel's publishing authorization that way.
 *
 * Four independent separations now apply, none of which relies on any
 * individual test behaving carefully:
 *
 *   database  a dedicated e2e-test.db; app.core.runtime_mode makes a backend
 *             that resolves ACE_DB_PATH to the operational database refuse to
 *             start at all
 *   ports     backend :8100, frontend :5273 — the live :8000/:5173 pair is
 *             never touched, and reuseExistingServer is off so a stray server
 *             is never adopted
 *   mode      ACE_TEST_MODE=e2e refuses operations with effects outside the DB
 *   providers fake AI/TTS, publishing and release gates hard off, provider API
 *             keys unset in the backend process
 */

const PORT_BACKEND = Number(process.env.ACE_E2E_BACKEND_PORT ?? 8100)
const PORT_FRONTEND = Number(process.env.ACE_E2E_FRONTEND_PORT ?? 5273)

export const E2E_BACKEND_URL = `http://127.0.0.1:${PORT_BACKEND}`
export const E2E_FRONTEND_URL = `http://localhost:${PORT_FRONTEND}`

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [
    ['html', { open: 'never' }],
    ['list'],
  ],
  use: {
    baseURL: E2E_FRONTEND_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    // Dev auth header — accepted by backend when ACE_ENV=development.
    extraHTTPHeaders: {
      'X-Dev-Actor': 'dev:studio-user',
    },
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: [
    {
      // Never scripts/start-backend.sh: that one sources .env.local, which
      // carries live API keys and live publishing gates.
      command: 'bash scripts/start-e2e-backend.sh',
      url: `${E2E_BACKEND_URL}/api/health`,
      timeout: 60_000,
      // Deliberately false even locally. Reusing whatever happens to be
      // listening is exactly how the suite reached the live backend.
      reuseExistingServer: false,
      cwd: '..',
      stdout: 'pipe',
      stderr: 'pipe',
    },
    {
      command: `npm run dev -- --port ${PORT_FRONTEND} --strictPort`,
      url: E2E_FRONTEND_URL,
      timeout: 60_000,
      reuseExistingServer: false,
      env: {
        // Vite proxies /api to whatever this names; without it the E2E
        // frontend would proxy to the LIVE backend on :8000.
        ACE_BACKEND_URL: E2E_BACKEND_URL,
      },
    },
  ],
})
