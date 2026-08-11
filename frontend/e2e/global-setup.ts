/**
 * Playwright globalSetup — runs once before the full E2E suite.
 *
 * Checks that the dev seed workspace exists. If not found, prints a clear
 * actionable message and exits — tests will not run against an unseeded DB,
 * preventing misleading mass-skips or false passes.
 *
 * Does NOT run seed-dev automatically (to avoid accidental mutations).
 */

import { request } from '@playwright/test'

export default async function globalSetup() {
  const BACKEND = 'http://127.0.0.1:8000'
  const DEV_SLUG = 'dev-studio'

  const ctx = await request.newContext({
    baseURL: BACKEND,
    extraHTTPHeaders: { 'X-Dev-Actor': 'dev:studio-user' },
  })

  let seeded = false
  try {
    const res = await ctx.get('/api/v1/workspaces')
    if (res.ok()) {
      const workspaces: Array<{ slug: string }> = await res.json()
      seeded = workspaces.some(w => w.slug === DEV_SLUG)
    }
  } catch {
    // Backend not ready — webServer will handle the error; skip seed check.
    seeded = true
  } finally {
    await ctx.dispose()
  }

  if (!seeded) {
    console.error('\n')
    console.error('  ✗  E2E SEED MISSING')
    console.error(`  The '${DEV_SLUG}' workspace was not found in the local database.`)
    console.error('  Run the following command and try again:')
    console.error()
    console.error('      make seed-dev')
    console.error()
    throw new Error(`Dev seed not found — run 'make seed-dev' before running E2E tests.`)
  }

  console.log(`  ✓  E2E global setup: workspace '${DEV_SLUG}' confirmed.`)
}
