/**
 * Phase 17C — visual capture of the redesigned Analytics pages against the
 * real Orvella workspace data. Read-only: navigates and screenshots only.
 * No mutations, no publishing, no YouTube calls.
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

test.describe('Phase 17C visual capture — real Orvella data', () => {
  test('Analytics overview and per-video analytics', async ({ page, baseURL }) => {
    const wsId = await resolveRealWorkspaceId(baseURL!)
    test.skip(!wsId, 'local-dev workspace not found')

    await page.goto(`/workspaces/${wsId}/analytics`)
    await expect(page.getByRole('heading', { level: 1, name: /analytics/i })).toBeVisible({ timeout: 15_000 })
    await page.waitForTimeout(500) // let fanned-out per-publication analytics queries settle
    await page.screenshot({ path: 'e2e-artifacts/17c-analytics-overview.png', fullPage: true })

    // Open the video with the most observation history (publication 1 —
    // "Why Renewable Energy Is Getting So Cheap", 2 real data points) so the
    // history chart branch is captured, not just the insufficient-data one.
    const cards = page.locator('.video-card')
    const count = await cards.count()
    for (let i = 0; i < count; i++) {
      const title = await cards.nth(i).locator('.video-card-title').textContent()
      if (title?.includes('Renewable')) {
        await cards.nth(i).click()
        break
      }
    }
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 })
    await page.waitForTimeout(400)
    await page.screenshot({ path: 'e2e-artifacts/17c-video-analytics.png', fullPage: true })

    // Also capture a video with only one real observation, to document the
    // honest "not enough history yet" state (rather than a fabricated line).
    await page.goto(`/workspaces/${wsId}/analytics`)
    await expect(page.getByRole('heading', { level: 1, name: /analytics/i })).toBeVisible({ timeout: 15_000 })
    const publicCrispr = page.locator('.video-card', { hasText: 'Public' }).filter({ hasText: 'CRISPR' })
    if (await publicCrispr.count() > 0) {
      await publicCrispr.first().click()
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 })
      await page.waitForTimeout(400)
      await page.screenshot({ path: 'e2e-artifacts/17c-video-analytics-sparse.png', fullPage: true })
    }
  })
})
