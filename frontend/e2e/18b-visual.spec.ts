/**
 * Phase 18B — visual capture of the Channel page (Strategy, Automation &
 * Publishing Policy with the new Production automation state, and Autonomy
 * Readiness together) and the Pipelines page detail view for the newly
 * autonomously-produced pipeline, against the real Orvella workspace data.
 * Read-only: navigates, expands, and screenshots only. No mutations, no
 * publishing, no YouTube/LLM calls triggered by this test.
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

test.describe('Phase 18B visual capture — real Orvella data', () => {
  test('Channel page — Strategy, Automation & Publishing Policy (with production state), and Readiness', async ({ page, baseURL }) => {
    const wsId = await resolveRealWorkspaceId(baseURL!)
    test.skip(!wsId, 'local-dev workspace not found')

    await page.goto(`/workspaces/${wsId}/channel`)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('heading', { name: 'Strategy', level: 2 })).toBeVisible({ timeout: 10_000 })
    await expect(page.getByRole('heading', { name: 'Automation & Publishing Policy', level: 2 })).toBeVisible()
    await expect(page.getByText('Production automation', { exact: true })).toBeVisible()

    // Deliberately not asserting one specific slot label. A released slot
    // correctly leaves the queue (Phase 18D), so 'Ready for publishing' is a
    // transient state this page may or may not be showing on any given run —
    // pinning it made the spec assert a moment in the channel's life rather
    // than a property of the page.
    await expect(page.getByRole('heading', { name: 'Autonomy readiness', level: 2 })).toBeVisible()
    // The readiness surface is categorised (Phase 18D), so these must render.
    await expect(page.getByText('Decision readiness', { exact: true })).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('Autonomous public publishing', { exact: true })).toBeVisible()

    await page.getByRole('heading', { name: 'Strategy', level: 2 }).scrollIntoViewIfNeeded()
    await page.waitForTimeout(600)
    await page.screenshot({ path: 'e2e-artifacts/18b-strategy-automation-production-readiness.png', fullPage: true })
  })

  test('Pipelines page — the autonomously-produced pipeline, expanded to render/technical detail', async ({ page, baseURL }) => {
    const wsId = await resolveRealWorkspaceId(baseURL!)
    test.skip(!wsId, 'local-dev workspace not found')

    await page.goto(`/workspaces/${wsId}/pipelines`)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 })

    const card = page.getByRole('button', { name: /Pipeline 0f852f60/i })
    await expect(card).toBeVisible({ timeout: 10_000 })
    await card.click()
    await expect(page.getByText('Stage History')).toBeVisible()

    const renderRow = page.getByRole('button', { name: /expand Rendering/i })
    await renderRow.scrollIntoViewIfNeeded()
    await renderRow.click()
    await page.waitForTimeout(600)
    await page.screenshot({ path: 'e2e-artifacts/18b-pipeline-detail-render.png', fullPage: true })
  })
})
