/**
 * Learning page E2E tests.
 *
 * Requires: make seed-dev (seeded dev workspace with 3 pending recommendations)
 * Auth: dev auth bypass via X-Dev-Actor header — no real OAuth, no live publishing.
 * Data: DEV SEEDED DATA ONLY — no real workspace, no Orvella, no live data.
 */

import { test, expect } from './fixtures'
import { gotoAndReady, resolveWorkspaceOrSkip } from './helpers'

/** Wait for at least one recommendation card to appear in the main content area. */
async function waitForRecommendations(page: import('@playwright/test').Page) {
  await expect(page.locator('[data-testid^="recommendation-"]').first()).toBeVisible({
    timeout: 10_000,
  })
}

test.describe('Learning page', () => {
  let wsId: string

  test.beforeAll(async ({ baseURL }) => {
    wsId = await resolveWorkspaceOrSkip(baseURL)
  })

  test('navigates to learning and renders the page heading', async ({ page }) => {
    await gotoAndReady(page, wsId, 'learning')
    await expect(page.getByRole('heading', { name: /learning/i, level: 1 })).toBeVisible({
      timeout: 10_000,
    })
  })

  test('does not crash or show an error boundary', async ({ page }) => {
    await gotoAndReady(page, wsId, 'learning')
    await expect(page.getByText(/something went wrong|unhandled error/i)).not.toBeVisible()
  })

  test('shows seeded recommendations — populated state', async ({ page }) => {
    await gotoAndReady(page, wsId, 'learning')
    await waitForRecommendations(page)
    // Seed creates 3 recommendations; at least one card must be present.
    const cards = page.locator('[data-testid^="recommendation-"]')
    await expect(cards).toHaveCount(3, { timeout: 5_000 })
  })

  test('seeded data does not show unavailable state', async ({ page }) => {
    await gotoAndReady(page, wsId, 'learning')
    await waitForRecommendations(page)
    await expect(page.locator('[data-testid="unavailable-state"]')).not.toBeVisible()
  })

  test('status filter buttons appear when data is present', async ({ page }) => {
    await gotoAndReady(page, wsId, 'learning')
    await waitForRecommendations(page)
    await expect(page.getByTestId('filter-all')).toBeVisible()
    await expect(page.getByTestId('filter-pending')).toBeVisible()
    await expect(page.getByTestId('filter-accepted')).toBeVisible()
    await expect(page.getByTestId('filter-rejected')).toBeVisible()
  })

  test('filtering to accepted hides pending recommendations', async ({ page }) => {
    await gotoAndReady(page, wsId, 'learning')
    await waitForRecommendations(page)
    // All seeded recs are pending; accepted filter should show empty list.
    await page.getByTestId('filter-accepted').click()
    await expect(
      page.locator('[data-testid^="recommendation-"]').first(),
    ).not.toBeVisible({ timeout: 5_000 })
  })

  test('filtering back to all restores full recommendation list', async ({ page }) => {
    await gotoAndReady(page, wsId, 'learning')
    await waitForRecommendations(page)
    await page.getByTestId('filter-accepted').click()
    await page.getByTestId('filter-all').click()
    await waitForRecommendations(page)
    await expect(page.locator('[data-testid^="recommendation-"]')).toHaveCount(3, {
      timeout: 5_000,
    })
  })

  test('accept button triggers accept flow and updates status badge', async ({ page }) => {
    await gotoAndReady(page, wsId, 'learning')
    await waitForRecommendations(page)
    // Accept button only appears on pending recommendations. Skip if all already reviewed.
    const acceptBtn = page.locator('[data-testid^="accept-"]').first()
    const hasPending = await acceptBtn.isVisible({ timeout: 5_000 }).catch(() => false)
    if (!hasPending) {
      test.skip()
      return
    }
    await acceptBtn.click()
    // After accepting, switch to accepted filter and verify the card appears.
    await page.getByTestId('filter-accepted').click()
    await expect(
      page.locator('[data-testid^="recommendation-"]').first(),
    ).toBeVisible({ timeout: 8_000 })
  })

  test('reject button opens notes textarea — confirm disabled until text entered', async ({
    page,
  }) => {
    await gotoAndReady(page, wsId, 'learning')
    await waitForRecommendations(page)
    // Reject button only appears on pending recommendations. Skip if all already reviewed.
    const rejectOpenBtn = page.locator('[data-testid^="reject-open-"]').first()
    const hasPending = await rejectOpenBtn.isVisible({ timeout: 5_000 }).catch(() => false)
    if (!hasPending) {
      test.skip()
      return
    }
    await rejectOpenBtn.click()
    const notesArea = page.locator('[data-testid^="reject-notes-"]').first()
    await expect(notesArea).toBeVisible()
    const confirmBtn = page.locator('[data-testid^="reject-confirm-"]').first()
    await expect(confirmBtn).toBeDisabled()
  })

  test('reject confirm button enables after typing notes', async ({ page }) => {
    await gotoAndReady(page, wsId, 'learning')
    await waitForRecommendations(page)
    // Reject button only appears on pending recommendations. Skip if all already reviewed.
    const rejectOpenBtn = page.locator('[data-testid^="reject-open-"]').first()
    const hasPending = await rejectOpenBtn.isVisible({ timeout: 5_000 }).catch(() => false)
    if (!hasPending) {
      test.skip()
      return
    }
    await rejectOpenBtn.click()
    await page.locator('[data-testid^="reject-notes-"]').first().fill('Not applicable right now')
    const confirmBtn = page.locator('[data-testid^="reject-confirm-"]').first()
    await expect(confirmBtn).not.toBeDisabled()
  })

  test('confidence and evidence model section is always visible', async ({ page }) => {
    await gotoAndReady(page, wsId, 'learning')
    await expect(page.getByText('Confidence & Evidence Model')).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('heuristic signal strength')).toBeVisible()
    await expect(page.getByText('they describe associations, not causes')).toBeVisible()
  })
})
