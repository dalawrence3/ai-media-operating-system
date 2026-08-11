/**
 * E2E test helpers.
 * All tests use dev auth (X-Dev-Actor: dev:studio-user) — no real OAuth,
 * no real YouTube calls, no live publishing.
 */

import { type Page, expect, test } from '@playwright/test'

export const DEV_WORKSPACE_SLUG = 'dev-studio'
const BACKEND_PORT = 8000

// ── Workspace resolution ────────────────────────────────────────────────────

/**
 * Resolve the seeded dev workspace ID from the backend API.
 * Returns the id of the workspace with slug 'dev-studio', or null if not seeded.
 */
export async function getDevWorkspaceId(baseURL: string): Promise<string | null> {
  const backendBase = baseURL.replace(`:5173`, `:${BACKEND_PORT}`)
  try {
    const res = await fetch(`${backendBase}/api/v1/workspaces`, {
      headers: { 'X-Dev-Actor': 'dev:studio-user' },
    })
    if (!res.ok) return null
    const workspaces: Array<{ slug: string; id: string }> = await res.json()
    return workspaces.find(w => w.slug === DEV_WORKSPACE_SLUG)?.id ?? workspaces[0]?.id ?? null
  } catch {
    return null
  }
}

/**
 * Resolve workspace or skip the test with a clear message.
 * Use in beforeEach/beforeAll.
 */
export async function resolveWorkspaceOrSkip(baseURL: string | undefined): Promise<string> {
  const id = await getDevWorkspaceId(baseURL ?? `http://localhost:${BACKEND_PORT}`)
  if (!id) test.skip(true, 'Dev workspace not seeded — run: make seed-dev')
  return id!
}

// ── Navigation helpers ──────────────────────────────────────────────────────

/**
 * Navigate to a workspace page and wait until the primary navigation landmark
 * is visible. This is the stable signal that the AppShell has mounted and
 * auth has resolved (dev auth bypass makes this immediate).
 */
export async function gotoWorkspacePage(
  page: Page,
  wsId: string,
  section = 'dashboard',
): Promise<void> {
  await page.goto(`/workspaces/${wsId}/${section}`)
  await expect(page.getByRole('navigation', { name: /primary navigation/i })).toBeVisible({
    timeout: 10_000,
  })
}

/**
 * Wait for any full-page loading spinner to disappear.
 * Safe to call even when no spinner is present.
 */
export async function waitForAppReady(page: Page): Promise<void> {
  // Wait until the loading-state element either never appears or goes away.
  await page
    .locator('.loading-state')
    .waitFor({ state: 'detached', timeout: 10_000 })
    .catch(() => {
      /* no spinner = already ready */
    })
}

/**
 * Assert that the page-level h1 heading is visible.
 * Targets h1 only (level: 1) to avoid conflicts with h2 section headings or
 * UnavailableState titles that share the same text pattern.
 */
export async function expectPageHeading(page: Page, pattern: RegExp): Promise<void> {
  await expect(page.getByRole('heading', { name: pattern, level: 1 })).toBeVisible({
    timeout: 10_000,
  })
}

/**
 * Navigate to a workspace page, wait for AppShell, then wait for the
 * loading spinner to clear. Use before asserting page-specific content.
 */
export async function gotoAndReady(
  page: Page,
  wsId: string,
  section: string,
): Promise<void> {
  await gotoWorkspacePage(page, wsId, section)
  await waitForAppReady(page)
}

// ── URL helpers ─────────────────────────────────────────────────────────────

/**
 * Extract workspace ID from the current URL.
 */
export function workspaceIdFromUrl(url: string): string {
  const m = url.match(/\/workspaces\/([^/]+)/)
  if (!m) throw new Error(`No workspace ID in URL: ${url}`)
  return m[1]
}
