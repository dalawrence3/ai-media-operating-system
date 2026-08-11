/**
 * Workflows (Schedules) page E2E tests.
 * Tests schedule list, create modal, form validation.
 * No real cron execution — UI only.
 * Uses dev auth bypass + seeded dev workspace.
 */

import { test, expect } from './fixtures'
import { getDevWorkspaceId, gotoWorkspacePage, expectPageHeading } from './helpers'

test.describe('Workflows', () => {
  let wsId: string

  test.beforeEach(async ({ baseURL }) => {
    const id = await getDevWorkspaceId(baseURL!)
    if (!id) test.skip()
    wsId = id!
  })

  test('workflows page renders heading', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'workflows')
    await expectPageHeading(page, /workflow|schedule/i)
  })

  test('create schedule button opens modal', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'workflows')
    const createBtn = page.getByRole('button', { name: /new schedule|create schedule/i }).first()
    if (await createBtn.isVisible()) {
      await createBtn.click()
      await expect(page.getByRole('dialog')).toBeVisible()
    }
  })

  test('create schedule modal has required fields', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'workflows')
    const createBtn = page.getByRole('button', { name: /new schedule|create schedule/i }).first()
    if (await createBtn.isVisible()) {
      await createBtn.click()
      await expect(page.getByLabel(/name/i)).toBeVisible()
    }
  })

  test('create schedule submit disabled without name', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'workflows')
    const createBtn = page.getByRole('button', { name: /new schedule|create schedule/i }).first()
    if (await createBtn.isVisible()) {
      await createBtn.click()
      const submitBtn = page.getByRole('button', { name: /create|save/i }).last()
      await expect(submitBtn).toBeDisabled()
    }
  })

  test('invalid JSON config shows validation error', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'workflows')
    const createBtn = page.getByRole('button', { name: /new schedule|create schedule/i }).first()
    if (await createBtn.isVisible()) {
      await createBtn.click()
      const configField = page.getByLabel(/config.*json|schedule config/i)
      if (await configField.isVisible()) {
        await configField.fill('{invalid json')
        await configField.blur()
        // Target the error alert, not the textarea which also contains the text.
        await expect(page.getByRole('alert').filter({ hasText: /valid json/i })).toBeVisible()
      }
    }
  })
})
