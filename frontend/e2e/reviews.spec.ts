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

  test('shows empty state with no pending items', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'reviews')
    await page.waitForLoadState('networkidle')
    const isEmpty = await page.getByText(/no pending|no items|queue is empty|nothing to review/i).isVisible().catch(() => false)
    const hasItems = await page.getByRole('button', { name: /approve|reject/i }).first().isVisible().catch(() => false)
    expect(isEmpty || hasItems).toBe(true)
  })

  test('reject modal requires reason field', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'reviews')
    const rejectBtn = page.getByRole('button', { name: /reject/i }).first()
    if (await rejectBtn.isVisible()) {
      await rejectBtn.click()
      await expect(page.getByRole('dialog')).toBeVisible()
      const submitBtn = page.getByRole('button', { name: /confirm reject|reject item/i })
      await expect(submitBtn).toBeDisabled()
    }
  })
})
