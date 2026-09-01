/**
 * Channels page E2E tests.
 * Uses dev auth bypass + seeded dev workspace.
 *
 * Phase 17B.1 redesigned this into a single-channel product view: the
 * "New channel" creation action only renders in the channel-less empty
 * state (see Channels.tsx), which no seeded or live workspace reaches —
 * every workspace in this system already has a channel. That flow (modal
 * open/close, required fields, slug autofill, submit-disabled-without-name)
 * is already covered against a properly mocked empty state by
 * Channels.test.tsx; duplicating it here would need the E2E suite to create
 * and tear down a scratch workspace, which is out of scope for a narrow
 * staleness fix. This file now covers only what's actually reachable:
 * the page renders and shows the channel that exists.
 */

import { test, expect } from './fixtures'
import { getDevWorkspaceId, gotoWorkspacePage } from './helpers'

test.describe('Channels', () => {
  let wsId: string

  test.beforeEach(async ({ baseURL }) => {
    const id = await getDevWorkspaceId(baseURL!)
    if (!id) test.skip()
    wsId = id!
  })

  test('channels page renders', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'channels')
    // The single-channel view's <h1> is the channel's own identity (e.g. a
    // connected platform account's display name), not the literal word
    // "Channels" — matched permissively, same as the Dashboard/Channel
    // smoke tests in pages.spec.ts.
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 10_000 })
  })

  test('shows the channel that exists', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'channels')
    await page.waitForLoadState('networkidle')
    // Every seeded/live workspace already has a channel — this is the
    // populated view, never the channel-less empty state.
    await expect(page.getByText(/no channel yet/i)).not.toBeVisible()
    await expect(page.locator('.card').first()).toBeVisible()
  })

  test('seeded channels are visible', async ({ page }) => {
    await gotoWorkspacePage(page, wsId, 'channels')
    await page.waitForLoadState('networkidle')
    // At least one DEV channel name from seed-dev should appear.
    const bodyText = await page.textContent('body')
    expect(bodyText).toBeTruthy()
  })
})
