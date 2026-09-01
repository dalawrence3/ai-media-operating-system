/* AppShell — sidebar navigation regression tests.
 *
 * Root cause of initial bug (2026-08-09):
 *   .app-shell used display:flex with no flex-direction (defaults to row).
 *   The .dev-auth-banner was the first flex item with z-index:1000. Per CSS
 *   Flexbox spec, flex items with z-index≠auto create stacking contexts even
 *   without position, so the banner (z-index:1000) rendered above the fixed
 *   sidebar (z-index:100). With align-items:stretch + min-height:100vh on the
 *   parent, the banner stretched to fill the full viewport height — a
 *   full-height purple column that covered all sidebar nav items.
 *   Fix: flex-direction:column so the banner is a top bar, not a side column.
 */

import { describe, it, expect, beforeAll } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { AppShell } from './AppShell'
import { WS_ID } from '@/test/fixtures'

// JSDOM does not implement window.matchMedia — polyfill it for AppShell's theme initializer.
beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
})

function wrap(wsId = WS_ID) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workspaces/${wsId}/dashboard`]}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<AppShell><div data-testid="page-content">content</div></AppShell>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('AppShell — sidebar navigation', () => {
  it('renders the primary navigation landmark', () => {
    wrap()
    expect(screen.getByRole('navigation', { name: /primary navigation/i })).toBeInTheDocument()
  })

  /* Phase 17A information architecture: the five day-to-day destinations are
     the only ones surfaced by default. Infrastructure moved under a collapsed
     Advanced group so it cannot compete with normal channel operations. */
  it('renders exactly the five primary destinations', () => {
    wrap()
    const nav = screen.getByRole('navigation', { name: /primary navigation/i })
    for (const label of ['Dashboard', 'Content', 'Analytics', 'Learn', 'Channel']) {
      expect(within(nav).getByRole('link', { name: label })).toBeInTheDocument()
    }
  })

  it('keeps infrastructure pages out of the default reading path', () => {
    wrap()
    const nav = screen.getByRole('navigation', { name: /primary navigation/i })
    // Collapsed under Advanced — present in the DOM but not exposed.
    expect(within(nav).queryByRole('link', { name: /pipelines/i })).not.toBeInTheDocument()
    expect(within(nav).queryByRole('link', { name: /audit/i })).not.toBeInTheDocument()
    expect(within(nav).queryByRole('link', { name: /settings/i })).not.toBeInTheDocument()
  })

  it('exposes infrastructure pages once Advanced is expanded', async () => {
    const user = userEvent.setup()
    wrap()
    const nav = screen.getByRole('navigation', { name: /primary navigation/i })

    const toggle = within(nav).getByRole('button', { name: /advanced/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')

    // Regex rather than exact match: badged items (Reviews, Exceptions) fold
    // their pending count into the accessible name.
    for (const label of [/pipelines/i, /reviews/i, /workflows/i, /audit/i, /settings/i]) {
      expect(within(nav).getByRole('link', { name: label })).toBeInTheDocument()
    }
  })

  it('rolls pending counts onto the collapsed Advanced toggle', async () => {
    // Moving Reviews/Exceptions out of the primary nav must not hide work that
    // needs attention: the collapsed group carries the combined count.
    // The counts arrive from queries, so this resolves asynchronously.
    wrap()
    const nav = screen.getByRole('navigation', { name: /primary navigation/i })
    expect(
      await within(nav).findByLabelText(/items need attention under advanced/i),
    ).toBeInTheDocument()
  })

  it('surfaces a global system status link instead of a primary Health tab', () => {
    wrap()
    const nav = screen.getByRole('navigation', { name: /primary navigation/i })
    expect(within(nav).queryByRole('link', { name: 'Health' })).not.toBeInTheDocument()
    // The status chip carries its meaning in words, not colour alone.
    expect(screen.getByRole('link', { name: /system status:/i })).toBeInTheDocument()
  })

  it('dev auth banner is present', () => {
    wrap()
    expect(screen.getByRole('banner', { name: /development mode indicator/i })).toBeInTheDocument()
  })

  it('page content slot is rendered', () => {
    wrap()
    expect(screen.getByTestId('page-content')).toBeInTheDocument()
  })

  it('nav links point to correct workspace-scoped paths', () => {
    wrap()
    const nav = screen.getByRole('navigation', { name: /primary navigation/i })
    const dashboardLink = within(nav).getByRole('link', { name: /dashboard/i })
    expect(dashboardLink).toHaveAttribute('href', `/workspaces/${WS_ID}/dashboard`)
  })
})

// ── CSS layout regression ────────────────────────────────────────────────────
// This test reads the CSS file directly to catch a reversion of the layout fix.
// The bug: .app-shell flex-direction defaults to row, making dev-auth-banner a
// full-height column that (via flex-item z-index) covers the sidebar entirely.

describe('AppShell CSS — layout direction regression', () => {
  const cssPath = join(process.cwd(), 'src/components/layout/AppShell.css')
  const css = readFileSync(cssPath, 'utf-8')

  it('.app-shell has flex-direction:column so the banner is a top bar, not a side column', () => {
    // Extract the .app-shell rule block and verify flex-direction:column is present.
    // Without this, the default flex-direction:row causes the dev-auth-banner
    // (z-index:1000 as a flex item) to render as a full-height column above
    // the sidebar (z-index:100), making all nav links invisible.
    const appShellBlock = css.match(/\.app-shell\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(appShellBlock).toMatch(/flex-direction\s*:\s*column/)
  })
})
