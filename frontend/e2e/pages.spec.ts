/**
 * Page-level smoke tests — verifies all routes render without crashing.
 * Uses dev auth bypass + seeded dev workspace.
 * Each test: navigate → expect page heading visible.
 * UnavailableState pages: assert the semantic heading and description text.
 */

import { test, expect } from './fixtures'
import { getDevWorkspaceId, gotoWorkspacePage, expectPageHeading } from './helpers'

type PageDef = { route: string; heading: RegExp }

const PAGES: PageDef[] = [
  { route: 'dashboard',   heading: /dashboard/i },
  { route: 'channels',    heading: /channels/i },
  { route: 'pipelines',   heading: /pipelines/i },
  { route: 'reviews',     heading: /review/i },
  { route: 'exceptions',  heading: /exception/i },
  { route: 'operations',  heading: /operations/i },
  { route: 'analytics',   heading: /analytics/i },
  { route: 'learning',    heading: /learning/i },
  { route: 'experiments', heading: /experiments/i },
  { route: 'workflows',   heading: /workflows/i },
  { route: 'publishing',  heading: /publishing/i },
  { route: 'health',      heading: /health/i },
  { route: 'audit',       heading: /audit/i },
  { route: 'settings',    heading: /settings/i },
]

test.describe('Page smoke tests', () => {
  let wsId: string

  test.beforeAll(async ({ baseURL }) => {
    const id = await getDevWorkspaceId(baseURL!)
    if (!id) {
      wsId = '__skip__'
      return
    }
    wsId = id
  })

  for (const { route, heading } of PAGES) {
    test(`${route} page renders without crash`, async ({ page }) => {
      if (wsId === '__skip__') test.skip()
      await gotoWorkspacePage(page, wsId, route)
      // Must not show an unhandled error boundary.
      await expect(page.getByText(/something went wrong|unhandled error/i)).not.toBeVisible()
      // Page-level heading must appear (waits up to 15s for data-dependent pages).
      await expectPageHeading(page, heading)
    })
  }

  test('publishing page shows intentional unavailable state', async ({ page }) => {
    if (wsId === '__skip__') test.skip()
    await gotoWorkspacePage(page, wsId, 'publishing')
    // UnavailableState renders an <h2> heading and a description paragraph.
    const unavailable = page.locator('[data-testid="unavailable-state"]')
    await expect(unavailable).toBeVisible({ timeout: 10_000 })
    await expect(unavailable.getByRole('heading')).toContainText(/live integration not configured/i)
  })

  // Note: analytics and learning pages show populated state when seed data
  // exists (make seed-dev). Detailed analytics/learning E2E live in
  // analytics.spec.ts and learning.spec.ts.
})
