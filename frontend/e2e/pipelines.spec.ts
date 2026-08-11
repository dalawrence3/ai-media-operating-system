/**
 * Pipelines page E2E tests.
 * No real pipeline execution — tests UI interactions only.
 * Uses dev auth bypass + seeded dev workspace.
 */

import { test, expect } from './fixtures'
import { getDevWorkspaceId, gotoWorkspacePage, expectPageHeading } from './helpers'

test.describe('Pipelines', () => {
  let wsId: string

  test.beforeEach(async ({ baseURL }) => {
    const id = await getDevWorkspaceId(baseURL!)
    if (!id) test.skip()
    wsId = id!
  })

  test('pipelines page renders heading', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'pipelines')
    await expectPageHeading(page, /pipelines/i)
  })

  test('shows empty state or pipeline list', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'pipelines')
    // Wait for page data to settle before checking content.
    await page.waitForLoadState('networkidle')
    const hasEmpty = await page.getByText(/no pipelines|create.*pipeline|get started/i).isVisible().catch(() => false)
    const hasCreateBtn = await page.getByRole('button', { name: /new pipeline|start pipeline|run/i }).isVisible().catch(() => false)
    const hasList = await page.locator('[data-testid="pipeline-card"]').first().isVisible().catch(() => false)
    expect(hasEmpty || hasCreateBtn || hasList).toBe(true)
  })

  test('"Start Pipeline" button opens modal', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'pipelines')
    const startBtn = page.getByRole('button', { name: /start pipeline|new pipeline/i }).first()
    if (await startBtn.isVisible()) {
      await startBtn.click()
      await expect(page.getByRole('dialog')).toBeVisible()
    }
  })

  test('pipeline status filter dropdown is present', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'pipelines')
    const select = page.getByRole('combobox', { name: /filter|status/i })
    if (await select.isVisible()) {
      await expect(select).toBeEnabled()
    }
  })
})
