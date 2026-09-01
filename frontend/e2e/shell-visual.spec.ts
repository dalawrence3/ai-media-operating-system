/**
 * Phase 17A — visual capture of the restructured product shell.
 *
 * Read-only: navigates and screenshots. Performs no mutations, no publishing,
 * no OAuth, and no YouTube calls. Uses the standard dev-auth bypass fixture.
 */

import { test, expect } from './fixtures'
import { getDevWorkspaceId } from './helpers'

test.describe('Phase 17A product shell', () => {
  test('captures the redesigned navigation', async ({ page, baseURL }) => {
    const wsId = await getDevWorkspaceId(baseURL!)
    test.skip(!wsId, 'Dev workspace not seeded')

    await page.goto(`/workspaces/${wsId}/dashboard`)
    const nav = page.getByRole('navigation', { name: /primary navigation/i })
    await expect(nav).toBeVisible()

    // The five primary destinations are the default reading path.
    for (const label of ['Dashboard', 'Content', 'Analytics', 'Learn', 'Channel']) {
      await expect(nav.getByRole('link', { name: label, exact: true })).toBeVisible()
    }

    // Infrastructure is collapsed away by default.
    await expect(nav.getByRole('link', { name: 'Audit' })).toBeHidden()

    // Global status chip replaces the primary Health tab.
    await expect(page.getByRole('link', { name: /system status:/i })).toBeVisible()

    await page.screenshot({
      path: 'e2e-artifacts/17a-shell-collapsed.png',
      fullPage: true,
    })

    // Expanding Advanced reveals the infrastructure pages.
    await nav.getByRole('button', { name: /advanced/i }).click()
    await expect(nav.getByRole('link', { name: 'Audit' })).toBeVisible()

    await page.screenshot({
      path: 'e2e-artifacts/17a-shell-advanced.png',
      fullPage: true,
    })
  })

  test('legacy routes redirect to the new information architecture', async ({
    page,
    baseURL,
  }) => {
    const wsId = await getDevWorkspaceId(baseURL!)
    test.skip(!wsId, 'Dev workspace not seeded')

    for (const [legacy, target] of [
      ['channels', 'channel'],
      ['learning', 'learn'],
      ['publishing', 'content'],
    ]) {
      await page.goto(`/workspaces/${wsId}/${legacy}`)
      await expect(page).toHaveURL(new RegExp(`/workspaces/${wsId}/${target}$`))
    }
  })
})
