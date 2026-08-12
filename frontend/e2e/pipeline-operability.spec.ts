/**
 * Pipeline operability E2E — topic management, artifact viewer, diagnostics.
 * Tests UI interactions only — no real pipeline execution, no live OAuth, no external calls.
 * Uses dev auth bypass + seeded dev workspace.
 */

import { test, expect } from './fixtures'
import { getDevWorkspaceId, gotoWorkspacePage } from './helpers'

test.describe('Pipeline operability — topic management', () => {
  let wsId: string

  test.beforeEach(async ({ baseURL }) => {
    const id = await getDevWorkspaceId(baseURL!)
    if (!id) test.skip()
    wsId = id!
  })

  test('Start Pipeline modal shows topic dropdown', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'pipelines')
    await expect(page.getByRole('button', { name: /start pipeline/i })).toBeVisible()
    await page.getByRole('button', { name: /start pipeline/i }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByLabel(/topic/i)).toBeVisible()
  })

  test('topic dropdown contains seeded topics', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'pipelines')
    await page.getByRole('button', { name: /start pipeline/i }).click()
    await page.waitForLoadState('networkidle')
    const topicSelect = page.getByLabel(/topic/i)
    const optionText = await topicSelect.textContent()
    // At least one DEV topic should be present after seeding
    const hasTopic = optionText?.includes('[DEV]') || optionText?.includes('AI Content')
    expect(hasTopic).toBeTruthy()
  })

  test('"+ Create new topic" shows inline create form', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'pipelines')
    await page.getByRole('button', { name: /start pipeline/i }).click()
    await page.waitForLoadState('networkidle')
    const topicSelect = page.getByLabel(/topic/i)
    await topicSelect.selectOption({ value: '__new__' })
    await expect(page.getByLabel(/new topic title/i)).toBeVisible()
    await expect(page.getByLabel(/topic angle/i)).toBeVisible()
  })

  test('inline create form can be discarded', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'pipelines')
    await page.getByRole('button', { name: /start pipeline/i }).click()
    await page.waitForLoadState('networkidle')
    await page.getByLabel(/topic/i).selectOption({ value: '__new__' })
    await expect(page.getByLabel(/new topic title/i)).toBeVisible()
    await page.getByRole('button', { name: /discard/i }).click()
    await expect(page.getByLabel(/new topic title/i)).not.toBeVisible()
  })

  test('cancel button closes modal', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'pipelines')
    await page.getByRole('button', { name: /start pipeline/i }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await page.getByRole('button', { name: /^cancel$/i }).click()
    await expect(page.getByRole('dialog')).not.toBeVisible()
  })
})

test.describe('Pipeline operability — stage history and artifact viewer', () => {
  let wsId: string

  test.beforeEach(async ({ baseURL }) => {
    const id = await getDevWorkspaceId(baseURL!)
    if (!id) test.skip()
    wsId = id!
  })

  test('clicking a pipeline card shows stage history', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'pipelines')
    await page.waitForLoadState('networkidle')
    // Pipeline cards have aria-pressed attribute; use CSS selector to target them reliably.
    const firstCard = page.locator('[aria-pressed]').first()
    if (!await firstCard.isVisible()) test.skip()
    await firstCard.click()
    await expect(page.getByText('Stage History')).toBeVisible()
  })

  test('stage rows have expand buttons', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'pipelines')
    await page.waitForLoadState('networkidle')
    const firstCard = page.locator('[aria-pressed]').first()
    if (!await firstCard.isVisible()) test.skip()
    await firstCard.click()
    await expect(page.getByText('Stage History')).toBeVisible()
    const expandButtons = page.getByRole('button', { name: /expand/i })
    const count = await expandButtons.count()
    // At least one completed stage should have an expand button
    expect(count).toBeGreaterThan(0)
  })

  test('expanding a stage shows artifact and diagnostics sections', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'pipelines')
    await page.waitForLoadState('networkidle')
    const firstCard = page.locator('[aria-pressed]').first()
    if (!await firstCard.isVisible()) test.skip()
    await firstCard.click()
    await expect(page.getByText('Stage History')).toBeVisible()
    const firstExpand = page.getByRole('button', { name: /expand/i }).first()
    if (!await firstExpand.isVisible()) test.skip()
    await firstExpand.click()
    // Expanded row should show at minimum the Diagnostics section label
    await expect(page.getByText('Diagnostics').first()).toBeVisible()
  })

  test('collapsing an expanded stage hides the detail panel', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'pipelines')
    await page.waitForLoadState('networkidle')
    const firstCard = page.locator('[aria-pressed]').first()
    if (!await firstCard.isVisible()) test.skip()
    await firstCard.click()
    await expect(page.getByText('Stage History')).toBeVisible()
    const firstExpand = page.getByRole('button', { name: /expand/i }).first()
    if (!await firstExpand.isVisible()) test.skip()
    await firstExpand.click()
    // Now collapse
    const collapseBtn = page.getByRole('button', { name: /collapse/i }).first()
    await expect(collapseBtn).toBeVisible()
    await collapseBtn.click()
    await expect(page.getByRole('button', { name: /collapse/i })).not.toBeVisible()
  })
})

test.describe('Pipeline operability — stage bar aria labels', () => {
  let wsId: string

  test.beforeEach(async ({ baseURL }) => {
    const id = await getDevWorkspaceId(baseURL!)
    if (!id) test.skip()
    wsId = id!
  })

  test('stage bar has 10 stage segments', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'pipelines')
    await page.waitForLoadState('networkidle')
    const stageList = page.getByRole('list', { name: /pipeline stages/i }).first()
    if (!await stageList.isVisible()) test.skip()
    const items = stageList.getByRole('listitem')
    await expect(items).toHaveCount(10)
  })
})
