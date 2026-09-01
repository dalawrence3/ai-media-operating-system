/**
 * Page-level smoke tests — verifies all routes render without crashing.
 * Uses dev auth bypass + seeded dev workspace.
 * Each test: navigate → expect page heading visible.
 * UnavailableState pages: assert the semantic heading and description text.
 */

import { test, expect } from './fixtures'
import { getDevWorkspaceId, gotoWorkspacePage, expectPageHeading } from './helpers'

type PageDef = { route: string; heading: RegExp }

// Phase 17B: dashboard's <h1> is now the channel's own identity (e.g. the
// connected YouTube account's display name), not the literal word
// "Dashboard" — so it's matched permissively here; the smoke test's job is
// to prove a heading rendered and the page didn't crash, not to assert a
// specific channel name that depends on dev-seed fixture content.
const PAGES: PageDef[] = [
  { route: 'dashboard',   heading: /.+/ },
  { route: 'content',     heading: /content/i },
  { route: 'analytics',   heading: /analytics/i },
  { route: 'learn',       heading: /learn/i },
  { route: 'channel',     heading: /.+/ },
  // Advanced / system pages — collapsed in the sidebar by default (Phase 17A)
  // but still directly reachable by URL.
  { route: 'pipelines',   heading: /pipelines/i },
  { route: 'reviews',     heading: /review/i },
  { route: 'exceptions',  heading: /exception/i },
  { route: 'operations',  heading: /operations/i },
  { route: 'experiments', heading: /experiments/i },
  { route: 'workflows',   heading: /workflows/i },
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

  // Note: analytics and learning pages show populated state when seed data
  // exists (make seed-dev). Detailed analytics/learning E2E live in
  // analytics.spec.ts and learning.spec.ts.
})
