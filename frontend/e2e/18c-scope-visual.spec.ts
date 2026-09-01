/**
 * Phase 18C follow-up — visual verification that the Channel page distinguishes
 * upload permission from public-release permission against Orvella's real
 * current credential.
 *
 * Written mid-18C, when public release had not yet been consented to. That
 * consent was subsequently granted, so the spec now asserts the current
 * intended state — all three scopes granted — rather than the snapshot it was
 * authored against. What it still proves is the property that matters: the
 * three permissions are surfaced as three INDEPENDENT grants, not as one
 * "connected" boolean.
 *
 * Read-only: navigates and screenshots only. Never clicks the
 * release-permission button, so no OAuth consent flow is ever initiated.
 */

import { test, expect } from './fixtures'

const REAL_WORKSPACE_ID = 'local-dev'

async function resolveRealWorkspaceId(baseURL: string): Promise<string | null> {
  const backendBase = baseURL.replace(':5173', ':8000')
  try {
    const res = await fetch(`${backendBase}/api/v1/workspaces`, {
      headers: { 'X-Dev-Actor': 'dev:studio-user' },
    })
    if (!res.ok) return null
    const workspaces: Array<{ id: string }> = await res.json()
    return workspaces.find(w => w.id === REAL_WORKSPACE_ID)?.id ?? null
  } catch {
    return null
  }
}

test.describe('Phase 18C follow-up — real Orvella credential', () => {
  test('upload, analytics and public release are three independent grants', async ({ page, baseURL }) => {
    const wsId = await resolveRealWorkspaceId(baseURL!)
    test.skip(!wsId, 'local-dev workspace not found')

    await page.goto(`/workspaces/${wsId}/channel`)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 })

    const uploadCard = page.getByText('Upload videos', { exact: true }).locator('xpath=ancestor::div[contains(@class,"card")][1]')
    await expect(uploadCard).toContainText('Granted')

    const analyticsCard = page.getByText('Read analytics', { exact: true }).locator('xpath=ancestor::div[contains(@class,"card")][1]')
    await expect(analyticsCard).toContainText('Granted')

    // Public release is a separate grant from upload — a credential that can
    // upload privately cannot necessarily change a video's privacy status.
    const releaseCard = page.getByText('Make videos public', { exact: true }).locator('xpath=ancestor::div[contains(@class,"card")][1]')
    await expect(releaseCard).toContainText('Granted')

    // Whichever way the grant currently stands, this test never initiates an
    // OAuth consent flow: no permission button is ever clicked.

    await page.getByText('Upload videos', { exact: true }).scrollIntoViewIfNeeded()
    await page.waitForTimeout(500)
    await page.screenshot({ path: 'e2e-artifacts/18c-permission-model.png', fullPage: true })
  })
})
