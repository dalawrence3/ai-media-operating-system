/**
 * Phase 17E — visual capture of the Channel page's Strategy section against
 * the real Orvella workspace data. Read-only: navigates and screenshots
 * only. No mutations, no publishing, no YouTube calls, no strategy edits.
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

test.describe('Phase 17E visual capture — real Orvella data', () => {
  test('Channel page — Strategy section', async ({ page, baseURL }) => {
    const wsId = await resolveRealWorkspaceId(baseURL!)
    test.skip(!wsId, 'local-dev workspace not found')

    await page.goto(`/workspaces/${wsId}/channel`)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('heading', { name: 'Strategy', level: 2 })).toBeVisible({ timeout: 10_000 })
    await page.waitForTimeout(500)
    await page.screenshot({ path: 'e2e-artifacts/17e-channel-overview.png', fullPage: true })

    // Open the edit modal to capture the editing experience (no save click).
    const editBtn = page.getByRole('button', { name: /edit strategy/i })
    if (await editBtn.isVisible().catch(() => false)) {
      await editBtn.click()
      await expect(page.getByRole('dialog')).toBeVisible()
      await page.screenshot({ path: 'e2e-artifacts/17e-strategy-edit-modal.png' })
      await page.getByRole('button', { name: /^cancel$/i }).click()
    }
  })
})
