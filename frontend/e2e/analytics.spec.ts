/**
 * Analytics page E2E tests — Phase 17C channel performance and
 * video-by-video view.
 *
 * The dev-seeded workspace (make seed-dev) has zero publications, so it
 * exercises the empty state honestly. Populated-state behavior (KPIs, the
 * video grid, navigation into a video's own analytics page) is verified
 * against the real 'local-dev' Orvella workspace, mirroring the pattern
 * already used by 17b-visual.spec.ts — read-only, no mutations.
 *
 * Auth: dev auth bypass via X-Dev-Actor header — no real OAuth, no live publishing.
 */

import { test, expect } from './fixtures'
import { gotoAndReady, resolveWorkspaceOrSkip } from './helpers'

test.describe('Analytics page — dev-seeded workspace (empty)', () => {
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

  test('shows an honest empty state when the channel has no videos', async ({ page }) => {
    await gotoAndReady(page, wsId, 'analytics')
    await expect(page.getByText(/no videos yet/i)).toBeVisible({ timeout: 10_000 })
  })
})

const REAL_WORKSPACE_ID = 'local-dev'

async function resolveRealWorkspaceId(baseURL: string): Promise<string | null> {
  const backendBase = baseURL.replace(':5173', ':8000')
  try {
    const res = await fetch(`${backendBase}/api/v1/workspaces`, {
      headers: { 'X-Dev-Actor': 'dev:studio-user' },
    })
    if (!res.ok) return null
    const workspaces: Array<{ id: string }> = await res.json()
    return workspaces.find(w => w.id === REAL_WORKSPACE_ID)?.id ?? null
  } catch {
    return null
  }
}

test.describe('Analytics page — real Orvella data (populated)', () => {
  test('shows channel KPIs, the video grid, and navigates into a video\'s own analytics', async ({ page, baseURL }) => {
    const wsId = await resolveRealWorkspaceId(baseURL!)
    test.skip(!wsId, 'local-dev workspace not found')

    await page.goto(`/workspaces/${wsId}/analytics`)
    await expect(page.getByRole('heading', { level: 1, name: /analytics/i })).toBeVisible({ timeout: 15_000 })

    // Channel KPI row.
    await expect(page.locator('.metric-card-label', { hasText: /^Views$/ })).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('.metric-card-label', { hasText: 'Watch time' })).toBeVisible()

    // Video performance grid.
    await expect(page.getByText('Video performance')).toBeVisible()
    const firstCard = page.locator('.video-card').first()
    await expect(firstCard).toBeVisible()

    // Clicking a video opens its own analytics page.
    await firstCard.click()
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('heading', { name: 'Performance', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Performance over time' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Retention', exact: true })).toBeVisible()
  })
})
