/**
 * Reviews page E2E tests.
 * No real review items created — tests empty state and form validation.
 * Uses dev auth bypass + seeded dev workspace.
 */

import { test, expect } from './fixtures'
import { getDevWorkspaceId, gotoWorkspacePage, expectPageHeading } from './helpers'

test.describe('Reviews', () => {
  let wsId: string

  test.beforeEach(async ({ baseURL }) => {
    const id = await getDevWorkspaceId(baseURL!)
    if (!id) test.skip()
    wsId = id!
  })

  test('reviews page renders heading', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'reviews')
    await expectPageHeading(page, /review/i)
  })

  test('shows empty state or a review queue table', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'reviews')
    await page.waitForLoadState('networkidle')
    // Approve/Reject only render for a *selected* row (see Reviews.tsx —
    // clicking a table row reveals the detail pane with those actions), so
    // their absence doesn't mean the queue is empty. Check for the empty
    // state or at least one selectable row instead.
    const isEmpty = await page.getByText(/no pending reviews/i).isVisible().catch(() => false)
    const hasRows = await page.locator('tbody tr[role="button"]').first().isVisible().catch(() => false)
    expect(isEmpty || hasRows).toBe(true)
  })

  test('reject modal requires reason field', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'reviews')
    await page.waitForLoadState('networkidle')
    const firstRow = page.locator('tbody tr[role="button"]').first()
    if (await firstRow.isVisible().catch(() => false)) {
      await firstRow.click()
      const rejectBtn = page.getByRole('button', { name: /reject/i }).first()
      await expect(rejectBtn).toBeVisible()
      await rejectBtn.click()
      await expect(page.getByRole('dialog')).toBeVisible()
      const submitBtn = page.getByRole('button', { name: /confirm reject|reject item/i })
      await expect(submitBtn).toBeDisabled()
    }
  })
})
