/**
 * E2E test fixtures.
 *
 * All specs import { test, expect } from './fixtures' instead of
 * '@playwright/test' so the auth-bypass script is injected automatically.
 *
 * Auth bypass: injects window.__ACE_E2E_DEV_AUTH = true via addInitScript
 * before every navigation. AuthContext checks this flag and skips the JWT
 * bootstrap, treating the session as authenticated immediately.
 *
 * No real OAuth, no real credentials, no live publishing.
 */

import { test as base, expect } from '@playwright/test'

export { expect }

export const test = base.extend({
  // eslint-disable-next-line react-hooks/rules-of-hooks
  page: async ({ page }, use) => {
    // Inject before any page script runs so AuthContext sees the flag on mount.
    await page.addInitScript(() => {
      ;(window as unknown as Record<string, unknown>).__ACE_E2E_DEV_AUTH = true
    })
    // eslint-disable-next-line react-hooks/rules-of-hooks
    await use(page)
  },
})
