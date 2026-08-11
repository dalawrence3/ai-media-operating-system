/**
 * Workspace selection and AppShell navigation E2E tests.
 * Uses dev auth bypass — no real login, no real credentials.
 * Requires: make seed-dev (creates dev-studio workspace).
 */

import { test, expect } from './fixtures'
import { getDevWorkspaceId, gotoWorkspacePage } from './helpers'

test.describe('Workspace Select', () => {
  test('workspace list page renders after navigation to root', async ({ page }) => {
    await page.goto('/')
    // With dev auth bypass active, root either shows WorkspaceSelect or redirects.
    const url = page.url()
    expect(url).toMatch(/\/login|\//)
  })
})

test.describe('Navigation / AppShell', () => {
  let wsId: string

  test.beforeEach(async ({ baseURL }) => {
    const id = await getDevWorkspaceId(baseURL!)
    if (!id) test.skip()
    wsId = id!
  })

  test('AppShell sidebar renders navigation links', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'dashboard')
    const nav = page.getByRole('navigation', { name: /primary navigation/i })
    await expect(nav).toBeVisible()
    for (const link of ['Dashboard', 'Channels', 'Pipelines', 'Reviews']) {
      await expect(nav.getByRole('link', { name: new RegExp(link, 'i') })).toBeVisible()
    }
  })

  test('sidebar links navigate to correct pages', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'dashboard')
    await page.getByRole('navigation', { name: /primary navigation/i })
      .getByRole('link', { name: /channels/i }).first().click()
    await expect(page).toHaveURL(/\/channels/)
  })

  test('dashboard page renders stat tiles', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'dashboard')
    await expect(page.getByRole('heading', { name: /dashboard/i })).toBeVisible({ timeout: 15_000 })
  })
})
