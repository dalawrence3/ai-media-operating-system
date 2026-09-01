/**
 * Learn page E2E tests — Phase 17D.
 *
 * Auth: dev auth bypass via X-Dev-Actor header — no real OAuth, no live publishing.
 *
 * Data source note: recommendations are now fetched by fanning out over the
 * current channel's actual publications (see useChannelRecommendations),
 * not the old workspace-wide/topic-scoped endpoint. This closes a
 * cross-workspace leak (recs attached to a mis-scoped topic_id used to leak
 * between workspaces — see Phase 17B/17D reports) but means the dev-studio
 * seed workspace, which has zero publications, now honestly shows an empty
 * state rather than displaying recommendations that don't actually belong
 * to any of its own videos. So: dev-studio covers empty-state and smoke
 * behavior; populated-state and filter behavior are verified read-only
 * against the real local-dev/Orvella workspace, which does have
 * publication-scoped recommendations. Accept/reject mutation flows are
 * intentionally NOT exercised here against either workspace — dev-studio
 * can no longer supply compatible fixture data for them, and mutating real
 * Orvella recommendation state from an E2E run is unsafe. That interaction
 * is already covered thoroughly (accept, reject-notes-required, badge
 * updates) by the MSW-mocked unit suite in Learning.test.tsx.
 */

import { test, expect } from './fixtures'
import { gotoAndReady, resolveWorkspaceOrSkip } from './helpers'

test.describe('Learn page — dev-seeded workspace (empty)', () => {
  let wsId: string

  test.beforeAll(async ({ baseURL }) => {
    wsId = await resolveWorkspaceOrSkip(baseURL)
  })

  test('navigates to learn and renders the page heading', async ({ page }) => {
    await gotoAndReady(page, wsId, 'learn')
    await expect(page.getByRole('heading', { name: 'Learn', level: 1 })).toBeVisible({
      timeout: 10_000,
    })
  })

  test('does not crash or show an error boundary', async ({ page }) => {
    await gotoAndReady(page, wsId, 'learn')
    await expect(page.getByText(/something went wrong|unhandled error/i)).not.toBeVisible()
  })

  test('shows an honest empty state for recommendations — no publications, no leaked data', async ({ page }) => {
    await gotoAndReady(page, wsId, 'learn')
    await expect(page.getByText(/no recommendations yet/i)).toBeVisible({ timeout: 10_000 })
  })

  test('confidence and evidence model section is always visible', async ({ page }) => {
    await gotoAndReady(page, wsId, 'learn')
    await expect(page.getByText('Confidence & evidence model')).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('heuristic signal strength')).toBeVisible()
    const evidenceCard = page.locator('.diagnostic-finding', { hasText: 'Evidence Classification' })
    await expect(evidenceCard.getByText(/they describe associations, not causes/i)).toBeVisible()
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

test.describe('Learn page — real Orvella data (populated, read-only)', () => {
  test('shows real recommendations and supports status filtering without mutating anything', async ({ page, baseURL }) => {
    const wsId = await resolveRealWorkspaceId(baseURL!)
    test.skip(!wsId, 'local-dev workspace not found')

    await page.goto(`/workspaces/${wsId}/learn`)
    await expect(page.getByRole('heading', { name: 'Learn', level: 1 })).toBeVisible({ timeout: 15_000 })

    const cards = page.locator('[data-testid^="recommendation-"]')
    await expect(cards.first()).toBeVisible({ timeout: 10_000 })
    const fullCount = await cards.count()
    expect(fullCount).toBeGreaterThan(0)

    // Filtering is a client-side read; safe to exercise against live data.
    await page.getByTestId('filter-accepted').click()
    await expect(page.locator('[data-testid^="accept-"]')).toHaveCount(0, { timeout: 5_000 })

    await page.getByTestId('filter-all').click()
    await expect(cards).toHaveCount(fullCount, { timeout: 5_000 })
  })
})
