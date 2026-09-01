/**
 * Phase 18C — visual capture of the Channel page showing all three automation
 * states with the new public-publishing authorization card, against the real
 * Orvella workspace data. Read-only: navigates and screenshots only. Performs
 * no authorization change, no publishing, no YouTube calls.
 */

import { test, expect } from './fixtures'

const REAL_WORKSPACE_ID = 'local-dev'
const ORVELLA_CHANNEL_ID = '623a13aa-eaf6-4b3c-b546-6f4b1a666fa5'

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

test.describe('Phase 18C visual capture — real Orvella data', () => {
  test('Channel page — automation states with publishing authorization detail', async ({ page, baseURL }) => {
    const wsId = await resolveRealWorkspaceId(baseURL!)
    test.skip(!wsId, 'local-dev workspace not found')

    await page.goto(`/workspaces/${wsId}/channel`)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('heading', { name: 'Automation & Publishing Policy', level: 2 })).toBeVisible()
    await expect(page.getByText('Public publishing authorization')).toBeVisible()

    // The card must render the affordance matching the channel's ACTUAL state.
    // Orvella was authorized in Phase 18D, so pinning the un-authorized
    // affordance would assert a state the channel has deliberately left.
    // Whichever state it is in, exactly one control is offered and the live
    // authorization decision is shown.
    // Read-only throughout: this test never clicks a control in the
    // authorization card. Exactly one affordance is offered, matching whichever
    // state the channel is actually in.
    const authorize = page.getByRole('button', { name: /authorize…/i })
    const turnOff = page.getByRole('button', { name: /turn off/i })
    await expect(authorize.or(turnOff).first()).toBeVisible({ timeout: 10_000 })

    await page.getByText('Public publishing authorization').scrollIntoViewIfNeeded()
    await page.waitForTimeout(600)
    await page.screenshot({ path: 'e2e-artifacts/18c-publishing-authorization.png', fullPage: true })
  })

  test('Authorization confirmation dialog states the consequence plainly', async ({ page, baseURL }) => {
    const wsId = await resolveRealWorkspaceId(baseURL!)
    test.skip(!wsId, 'local-dev workspace not found')

    // Decide from the API, not the DOM.
    //
    // The authorization card renders its un-authorized branch ("Authorize…")
    // while React Query is still fetching, so a DOM-based guard can read that
    // transient state and fall through to clicking a control on a channel that
    // is in fact authorized. That is not a cosmetic flake: on a live channel
    // the control present in that card is "Turn off", and a mis-timed click
    // REVOKES production publishing authorization. It did exactly that during
    // Phase 18D activation.
    //
    // The API answer is settled before the page is even opened, so there is no
    // window in which this test can act on the wrong state.
    const backendBase = baseURL!.replace(':5173', ':8000')
    const authRes = await fetch(
      `${backendBase}/api/v1/workspaces/${wsId}/channels/${ORVELLA_CHANNEL_ID}/publishing-authorization`,
      { headers: { 'X-Dev-Actor': 'dev:studio-user' } },
    )
    const authBody = await authRes.json()
    const isAuthorized = Boolean(authBody?.authorization?.authorized)

    // This test can only exercise the GRANT dialog, which exists only while the
    // channel is un-authorized. Never touch the card otherwise.
    test.skip(isAuthorized, 'channel is authorized; the grant dialog is not offered')

    await page.goto(`/workspaces/${wsId}/channel`)
    const authorize = page.getByRole('button', { name: /authorize…/i })
    await expect(authorize).toBeVisible({ timeout: 15_000 })
    await authorize.click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByRole('dialog')).toContainText(/without asking first/i)
    // Confirm stays disabled until the operator types the confirmation word.
    await expect(page.getByRole('button', { name: /^authorize publishing$/i })).toBeDisabled()

    await page.waitForTimeout(400)
    await page.screenshot({ path: 'e2e-artifacts/18c-authorization-dialog.png' })

    // Leave the page exactly as found — nothing is authorized by this test.
    await page.getByRole('button', { name: /^cancel$/i }).click()
  })
})
