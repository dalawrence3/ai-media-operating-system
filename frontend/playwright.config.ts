import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright E2E configuration.
 *
 * E2E tests use dev auth (X-Dev-Actor: dev:studio-user) and the fake AI/TTS
 * providers. No real OAuth flows, no live YouTube calls, no real publishing.
 *
 * webServer spins up both servers automatically — no 'make dev' needed.
 * Run: make e2e
 */

const PORT_BACKEND  = 8000
const PORT_FRONTEND = 5173

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
    baseURL: `http://localhost:${PORT_FRONTEND}`,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    // Dev auth header — accepted by backend when ACE_ENV=development.
    extraHTTPHeaders: {
      'X-Dev-Actor': 'dev:studio-user',
    },
  },

  // Shared browser config.
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  // Start backend then frontend before running tests.
  webServer: [
    {
      command: 'bash scripts/start-backend.sh',
      url: `http://127.0.0.1:${PORT_BACKEND}/api/health`,
      timeout: 30_000,
      reuseExistingServer: !process.env.CI,
      env: {
        ACE_ENV: 'development',
        ACE_DEV_AUTH: 'enabled',
        ACE_AI_PROVIDER: 'fake',
        ACE_TTS_PROVIDER: 'fake',
        ACE_TTS_LIVE_ENABLED: 'false',
        ACE_PUBLISHING_LIVE_ENABLED: 'false',
        ACE_DRY_RUN: '1',
        ACE_LOG_FORMAT: 'console',
        ACE_FRONTEND_URL: `http://localhost:${PORT_FRONTEND}`,
      },
      cwd: '..',
    },
    {
      command: 'npm run dev -- --port 5173',
      url: `http://localhost:${PORT_FRONTEND}`,
      timeout: 30_000,
      reuseExistingServer: !process.env.CI,
    },
  ],
})
