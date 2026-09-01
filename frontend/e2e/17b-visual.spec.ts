/**
 * Phase 17B — visual capture of Dashboard, Content, and Content detail
 * against the real Orvella workspace data. Read-only: navigates and
 * screenshots only. No mutations, no publishing, no YouTube calls.
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

test.describe('Phase 17B visual capture — real Orvella data', () => {
  test('Dashboard, Content, and Content detail', async ({ page, baseURL }) => {
    const wsId = await resolveRealWorkspaceId(baseURL!)
    test.skip(!wsId, 'local-dev workspace not found')

    await page.goto(`/workspaces/${wsId}/dashboard`)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 })
    await page.waitForTimeout(500) // let fanned-out analytics queries settle
    await page.screenshot({ path: 'e2e-artifacts/17b-dashboard.png', fullPage: true })

    await page.goto(`/workspaces/${wsId}/content`)
    await expect(page.getByRole('heading', { level: 1, name: 'Content' })).toBeVisible({ timeout: 15_000 })
    await page.waitForTimeout(300)
    await page.screenshot({ path: 'e2e-artifacts/17b-content-all.png', fullPage: true })

    const publishedTab = page.getByRole('tab', { name: /on youtube/i })
    if (await publishedTab.isVisible()) {
      await publishedTab.click()
      await page.screenshot({ path: 'e2e-artifacts/17b-content-published.png', fullPage: true })
    }

    // Open the first video card to capture the detail page.
    const firstCard = page.locator('.video-card').first()
    if (await firstCard.isVisible()) {
      await firstCard.click()
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 })
      await page.waitForTimeout(300)
      await page.screenshot({ path: 'e2e-artifacts/17b-content-detail.png', fullPage: true })
    }

    // Channel page — productized single-channel view (Phase 17B.1)
    await page.goto(`/workspaces/${wsId}/channel`)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 })
    await page.waitForTimeout(300)
    await page.screenshot({ path: 'e2e-artifacts/17b1-channel.png', fullPage: true })
  })
})
