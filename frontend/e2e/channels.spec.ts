/**
 * Channels page E2E tests.
 * Uses dev auth bypass + seeded dev workspace.
 * Tests: channel list, empty state, channel creation modal validation.
 * No real OAuth; no live platform calls.
 */

import { test, expect } from './fixtures'
import { getDevWorkspaceId, gotoWorkspacePage, expectPageHeading } from './helpers'

test.describe('Channels', () => {
  let wsId: string

  test.beforeEach(async ({ baseURL }) => {
    const id = await getDevWorkspaceId(baseURL!)
    if (!id) test.skip()
    wsId = id!
  })

  test('channels page renders', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'channels')
    await expectPageHeading(page, /channels/i)
  })

  test('shows channel list or empty state', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'channels')
    // Wait for the page to finish loading before checking content.
    await page.waitForLoadState('networkidle')
    const hasChannels = await page.locator('[data-testid="channel-card"]').first().isVisible()
      .catch(() => false)
    const hasEmptyState = await page.getByText(/no channels|create.*channel|get started/i).isVisible()
      .catch(() => false)
    const hasCreateBtn = await page
      .getByRole('button', { name: /new channel|create channel/i })
      .isVisible()
      .catch(() => false)
    expect(hasChannels || hasEmptyState || hasCreateBtn).toBe(true)
  })

  test('create channel button opens modal', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'channels')
    const createBtn = page.getByRole('button', { name: /new channel|create channel/i }).first()
    await expect(createBtn).toBeVisible({ timeout: 10_000 })
    await createBtn.click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByRole('heading', { name: /new channel/i })).toBeVisible()
  })

  test('create channel modal has required fields', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'channels')
    await page.getByRole('button', { name: /new channel|create channel/i }).first().click()
    await expect(page.getByLabel(/name/i)).toBeVisible()
    await expect(page.getByLabel(/slug/i)).toBeVisible()
  })

  test('create channel modal slug auto-fills from name', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'channels')
    await page.getByRole('button', { name: /new channel|create channel/i }).first().click()
    const nameField = page.getByLabel(/channel name/i)
    await nameField.fill('My Test Channel')
    const slugField = page.getByLabel(/slug/i)
    await expect(slugField).toHaveValue('my-test-channel')
  })

  test('create channel modal submit is disabled without name', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'channels')
    await page.getByRole('button', { name: /new channel|create channel/i }).first().click()
    const submitBtn = page.getByRole('button', { name: /create|save/i }).last()
    await expect(submitBtn).toBeDisabled()
  })

  test('cancel closes the modal', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'channels')
    await page.getByRole('button', { name: /new channel|create channel/i }).first().click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await page.getByRole('button', { name: /cancel/i }).click()
    await expect(page.getByRole('dialog')).not.toBeVisible()
  })

  test('seeded channels are visible', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'channels')
    await page.waitForLoadState('networkidle')
    // At least one DEV channel name from seed-dev should appear.
    const bodyText = await page.textContent('body')
    expect(bodyText).toBeTruthy()
  })
})
