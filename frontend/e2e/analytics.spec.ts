/**
 * Analytics page E2E tests.
 *
 * Requires: make seed-dev (seeded dev workspace with analytics aggregates)
 * Auth: dev auth bypass via X-Dev-Actor header — no real OAuth, no live publishing.
 *
 * Validates:
 * - Navigation to Analytics from app shell
 * - Seeded analytics aggregates are visible (populated state)
 * - UnavailableState is NOT shown when data exists
 * - Metric semantics table is always visible
 * - Period group headings rendered
 */

import { test, expect } from './fixtures'
import { gotoAndReady, resolveWorkspaceOrSkip } from './helpers'

test.describe('Analytics page', () => {
  let wsId: string

  test.beforeAll(async ({ baseURL }) => {
    wsId = await resolveWorkspaceOrSkip(baseURL)
  })

  test('navigates to analytics and renders the page heading', async ({ page }) => {
    await gotoAndReady(page, wsId, 'analytics')
    await expect(page.getByRole('heading', { name: /analytics/i, level: 1 })).toBeVisible({
      timeout: 10_000,
    })
  })

  test('does not crash or show an error boundary', async ({ page }) => {
    await gotoAndReady(page, wsId, 'analytics')
    await expect(page.getByText(/something went wrong|unhandled error/i)).not.toBeVisible()
  })

  test('shows seeded aggregate data — populated state', async ({ page }) => {
    await gotoAndReady(page, wsId, 'analytics')
    // The seed creates lifetime aggregates; expect a period group heading.
    await expect(page.getByRole('heading', { name: /lifetime/i, level: 2 })).toBeVisible({
      timeout: 10_000,
    })
  })

  test('seeded data does not show unavailable state', async ({ page }) => {
    await gotoAndReady(page, wsId, 'analytics')
    // Wait for data to render before asserting absence.
    await expect(page.getByRole('heading', { name: /lifetime/i, level: 2 })).toBeVisible({
      timeout: 10_000,
    })
    await expect(page.locator('[data-testid="unavailable-state"]')).not.toBeVisible()
  })

  test('seeded views metric is rendered in the aggregates table', async ({ page }) => {
    await gotoAndReady(page, wsId, 'analytics')
    await expect(page.getByRole('heading', { name: /lifetime/i, level: 2 })).toBeVisible({
      timeout: 10_000,
    })
    // The seeded lifetime aggregate for views should appear as a formatted number.
    // seed: 42_000 → formatted as "42.0K"
    await expect(page.getByText('42.0K')).toBeVisible({ timeout: 5_000 })
  })

  test('metric semantics table is always visible alongside data', async ({ page }) => {
    await gotoAndReady(page, wsId, 'analytics')
    await expect(page.getByRole('heading', { name: /lifetime/i, level: 2 })).toBeVisible({
      timeout: 10_000,
    })
    await expect(page.getByText('Metric Semantics')).toBeVisible()
  })

  test('monthly period group is rendered for seeded monthly aggregates', async ({ page }) => {
    await gotoAndReady(page, wsId, 'analytics')
    // Seed contains monthly aggregates for 2026-08 and 2026-07.
    await expect(page.getByRole('heading', { name: /monthly/i, level: 2 })).toBeVisible({
      timeout: 10_000,
    })
  })
})
