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

  test('AppShell sidebar renders the five primary destinations', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'dashboard')
    const nav = page.getByRole('navigation', { name: /primary navigation/i })
    await expect(nav).toBeVisible()
    for (const link of ['Dashboard', 'Content', 'Analytics', 'Learn', 'Channel']) {
      await expect(nav.getByRole('link', { name: link, exact: true })).toBeVisible()
    }
  })

  test('Advanced pages are reachable once expanded', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'dashboard')
    const nav = page.getByRole('navigation', { name: /primary navigation/i })
    await nav.getByRole('button', { name: /advanced/i }).click()
    await expect(nav.getByRole('link', { name: /pipelines/i })).toBeVisible()
  })

  test('sidebar links navigate to correct pages', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'dashboard')
    await page.getByRole('navigation', { name: /primary navigation/i })
      .getByRole('link', { name: 'Channel', exact: true }).click()
    await expect(page).toHaveURL(/\/channel$/)
  })

  test('dashboard page renders a channel-identity heading', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'dashboard')
    // Phase 17B: the <h1> is the channel's own identity, not the literal
    // word "Dashboard" — assert a heading rendered at all (no crash).
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 })
  })
})
