/**
 * Phase 17G — visual capture of the Channel page's Autonomy Readiness
 * section against the real Orvella workspace data. Read-only: navigates and
 * screenshots only. No mutations, no publishing, no YouTube/LLM calls
 * triggered by this test.
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

test.describe('Phase 17G visual capture — real Orvella data', () => {
  test('Channel page — Autonomy readiness section', async ({ page, baseURL }) => {
    const wsId = await resolveRealWorkspaceId(baseURL!)
    test.skip(!wsId, 'local-dev workspace not found')

    await page.goto(`/workspaces/${wsId}/channel`)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('heading', { name: 'Autonomy readiness', level: 2 })).toBeVisible({ timeout: 10_000 })
    await page.getByRole('heading', { name: 'Autonomy readiness', level: 2 }).scrollIntoViewIfNeeded()
    await page.waitForTimeout(600)
    await page.screenshot({ path: 'e2e-artifacts/17g-autonomy-readiness.png', fullPage: false })
  })
})
