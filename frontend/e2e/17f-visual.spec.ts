/**
 * Phase 17F — visual capture of the YouTube Trends evidence drill-down and
 * refresh-status line against the real Orvella workspace data. Read-only:
 * navigates and screenshots only. No mutations, no publishing, no YouTube
 * calls, no market refresh triggered by this test.
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

test.describe('Phase 17F visual capture — real Orvella data', () => {
  test('YouTube trends — refresh status and evidence drill-down', async ({ page, baseURL }) => {
    const wsId = await resolveRealWorkspaceId(baseURL!)
    test.skip(!wsId, 'local-dev workspace not found')

    await page.goto(`/workspaces/${wsId}/learn`)
    await expect(page.getByRole('heading', { level: 1, name: 'Learn' })).toBeVisible({ timeout: 15_000 })
    await page.getByRole('heading', { name: 'YouTube trends', level: 2 }).scrollIntoViewIfNeeded()
    await page.waitForTimeout(600)
    await page.screenshot({ path: 'e2e-artifacts/17f-youtube-trends.png', fullPage: true })

    const evidenceToggle = page.getByRole('button', { name: /evidence signal/i }).first()
    if (await evidenceToggle.isVisible().catch(() => false)) {
      await evidenceToggle.click()
      await expect(page.getByText(/external market evidence — why/i).first()).toBeVisible({ timeout: 10_000 })
      await page.waitForTimeout(300)
      await evidenceToggle.scrollIntoViewIfNeeded()
      await page.screenshot({ path: 'e2e-artifacts/17f-evidence-drilldown.png' })
    }
  })
})
