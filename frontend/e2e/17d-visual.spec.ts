/**
 * Phase 17D — visual capture of the redesigned Learn page against the real
 * Orvella workspace data. Read-only: navigates and screenshots only. No
 * mutations, no publishing, no YouTube calls, no accept/reject clicks.
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

test.describe('Phase 17D visual capture — real Orvella data', () => {
  test('Learn page — full scroll', async ({ page, baseURL }) => {
    const wsId = await resolveRealWorkspaceId(baseURL!)
    test.skip(!wsId, 'local-dev workspace not found')

    await page.goto(`/workspaces/${wsId}/learn`)
    await expect(page.getByRole('heading', { level: 1, name: 'Learn' })).toBeVisible({ timeout: 15_000 })
    await page.waitForTimeout(700) // let fanned-out per-publication + market intelligence queries settle
    await page.screenshot({ path: 'e2e-artifacts/17d-learn-overview.png', fullPage: true })
  })
})

test.describe('Phase 17D visual capture — dev-seeded empty states', () => {
  test('Learn page — honest empty state', async ({ page, baseURL }) => {
    const backendBase = baseURL!.replace(':5173', ':8000')
    const res = await fetch(`${backendBase}/api/v1/workspaces`, {
      headers: { 'X-Dev-Actor': 'dev:studio-user' },
    })
    const workspaces: Array<{ id: string; slug: string }> = await res.json()
    const devWs = workspaces.find(w => w.slug === 'dev-studio')
    test.skip(!devWs, 'dev-studio workspace not found')

    await page.goto(`/workspaces/${devWs!.id}/learn`)
    await expect(page.getByRole('heading', { level: 1, name: 'Learn' })).toBeVisible({ timeout: 15_000 })
    await page.waitForTimeout(500)
    await page.screenshot({ path: 'e2e-artifacts/17d-learn-empty.png', fullPage: true })
  })
})
