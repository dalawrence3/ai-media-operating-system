import { describe, it, expect, beforeAll, afterEach, afterAll } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'
import { PublicationDetail } from './PublicationDetail'
import {
  WS_ID,
  PUB_ID,
  publicationDetail,
  publicationAnalytics,
} from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

// ── Default handlers ──────────────────────────────────────────────────────────

const defaultHandlers = [
  http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}`, () =>
    HttpResponse.json(publicationDetail),
  ),
  http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/analytics`, () =>
    HttpResponse.json(publicationAnalytics),
  ),
  http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/stream`, () =>
    new HttpResponse(new Uint8Array([0, 1, 2]).buffer, {
      headers: { 'Content-Type': 'video/mp4' },
    }),
  ),
]

const server = setupServer(...defaultHandlers)

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function renderDetail(workspaceId = WS_ID, pubId = String(PUB_ID)) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workspaces/${workspaceId}/publishing/${pubId}`]}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/publishing/:publicationId"
            element={<PublicationDetail />}
          />
          <Route
            path="/workspaces/:workspaceId/publishing"
            element={<div data-testid="back-page" />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// ── Core rendering ────────────────────────────────────────────────────────────

describe('PublicationDetail rendering', () => {
  it('shows the publication title', async () => {
    renderDetail()
    await waitFor(() =>
      expect(screen.getByText('Why Renewable Energy Is Getting So Cheap')).toBeInTheDocument(),
    )
  })

  it('shows visibility and status', async () => {
    renderDetail()
    await waitFor(() => {
      expect(screen.getByText('private')).toBeInTheDocument()
      expect(screen.getByText('published')).toBeInTheDocument()
    })
  })

  it('shows tags', async () => {
    renderDetail()
    await waitFor(() => {
      expect(screen.getByText('#energy')).toBeInTheDocument()
      expect(screen.getByText('#tech')).toBeInTheDocument()
      expect(screen.getByText('#sustainability')).toBeInTheDocument()
    })
  })
})

// ── Release button: flags disabled ───────────────────────────────────────────

describe('Release button — flags disabled (release_enabled=false)', () => {
  it('renders a disabled Release Publicly button', async () => {
    // Default fixture: release_eligible=true, release_enabled=false, release_scope_granted=false
    renderDetail()
    await waitFor(() => screen.getByRole('button', { name: /release publicly/i }))
    expect(screen.getByRole('button', { name: /release publicly/i })).toBeDisabled()
  })

  it('shows "Release control not enabled" when release_enabled=false', async () => {
    renderDetail()
    await waitFor(() =>
      expect(screen.getByText(/release control not enabled/i)).toBeInTheDocument(),
    )
  })

  it('does not open modal when button is clicked while disabled', async () => {
    const user = userEvent.setup()
    renderDetail()
    await waitFor(() => screen.getByRole('button', { name: /release publicly/i }))
    await user.click(screen.getByRole('button', { name: /release publicly/i }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

// ── Release button: scope missing ────────────────────────────────────────────

describe('Release button — scope not granted', () => {
  it('shows scope-required message when release_scope_granted=false even if flags enabled', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}`, () =>
        HttpResponse.json({
          ...publicationDetail,
          release_eligible: true,
          release_enabled: true,
          release_scope_granted: false,
        }),
      ),
    )
    renderDetail()
    await waitFor(() =>
      expect(screen.getByText(/youtube release permission must be granted/i)).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: /release publicly/i })).toBeDisabled()
  })

  it('button disabled when scope missing regardless of feature flags', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}`, () =>
        HttpResponse.json({
          ...publicationDetail,
          release_eligible: true,
          release_enabled: true,
          release_scope_granted: false,
        }),
      ),
    )
    renderDetail()
    await waitFor(() => screen.getByRole('button', { name: /release publicly/i }))
    expect(screen.getByRole('button', { name: /release publicly/i })).toBeDisabled()
  })
})

// ── Release button: all three prerequisites ───────────────────────────────────

describe('Release button — all prerequisites met', () => {
  it('button is enabled when release_eligible AND release_enabled AND release_scope_granted', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}`, () =>
        HttpResponse.json({
          ...publicationDetail,
          release_eligible: true,
          release_enabled: true,
          release_scope_granted: true,
        }),
      ),
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/analytics`, () =>
        HttpResponse.json(publicationAnalytics),
      ),
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/stream`, () =>
        new HttpResponse(new Uint8Array([0]).buffer, { headers: { 'Content-Type': 'video/mp4' } }),
      ),
    )
    renderDetail()
    await waitFor(() => screen.getByRole('button', { name: /release publicly/i }))
    expect(screen.getByRole('button', { name: /release publicly/i })).not.toBeDisabled()
  })

  it('button disabled when scope_granted=true but enabled=false', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}`, () =>
        HttpResponse.json({
          ...publicationDetail,
          release_eligible: true,
          release_enabled: false,
          release_scope_granted: true,
        }),
      ),
    )
    renderDetail()
    await waitFor(() => screen.getByRole('button', { name: /release publicly/i }))
    expect(screen.getByRole('button', { name: /release publicly/i })).toBeDisabled()
  })
})

// ── Release button: not eligible ─────────────────────────────────────────────

describe('Release button — not eligible', () => {
  it('shows eligibility message when release_eligible=false with scope and flags enabled', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}`, () =>
        HttpResponse.json({
          ...publicationDetail,
          release_eligible: false,
          release_enabled: true,
          release_scope_granted: true,
        }),
      ),
    )
    renderDetail()
    await waitFor(() =>
      expect(screen.getByText(/publication must be in/i)).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: /release publicly/i })).toBeDisabled()
  })
})

// ── Release button: already public ───────────────────────────────────────────

describe('Release button — already public', () => {
  it('shows "Released publicly." message and disables button when visibility=public', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}`, () =>
        HttpResponse.json({
          ...publicationDetail,
          visibility: 'public',
          release_eligible: false,
          release_enabled: true,
          release_scope_granted: true,
        }),
      ),
    )
    renderDetail()
    await waitFor(() =>
      expect(screen.getByText(/released publicly/i)).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: /release publicly/i })).toBeDisabled()
  })
})

// ── Confirmation modal ────────────────────────────────────────────────────────

describe('Confirmation modal', () => {
  function activeHandlers() {
    return [
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}`, () =>
        HttpResponse.json({
          ...publicationDetail,
          release_eligible: true,
          release_enabled: true,
          release_scope_granted: true,
        }),
      ),
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/analytics`, () =>
        HttpResponse.json(publicationAnalytics),
      ),
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/stream`, () =>
        new HttpResponse(new Uint8Array([0]).buffer, { headers: { 'Content-Type': 'video/mp4' } }),
      ),
    ]
  }

  it('opens confirmation modal on button click', async () => {
    server.use(...activeHandlers())
    const user = userEvent.setup()
    renderDetail()
    await waitFor(() => screen.getByRole('button', { name: /release publicly/i }))
    const btn = screen.getByRole('button', { name: /release publicly/i })
    expect(btn).not.toBeDisabled()
    await user.click(btn)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText(/release video publicly/i)).toBeInTheDocument()
  })

  it('shows video title in modal', async () => {
    server.use(...activeHandlers())
    const user = userEvent.setup()
    renderDetail()
    await waitFor(() => screen.getByRole('button', { name: /release publicly/i }))
    await user.click(screen.getByRole('button', { name: /release publicly/i }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/why renewable energy is getting so cheap/i)).toBeInTheDocument()
  })

  it('closes modal on Cancel', async () => {
    server.use(...activeHandlers())
    const user = userEvent.setup()
    renderDetail()
    await waitFor(() => screen.getByRole('button', { name: /release publicly/i }))
    await user.click(screen.getByRole('button', { name: /release publicly/i }))
    await user.click(screen.getByRole('button', { name: /cancel/i }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('calls release endpoint on Confirm and shows success', async () => {
    server.use(
      ...activeHandlers(),
      http.post(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/release-public`, () =>
        HttpResponse.json({ visibility: 'public', reconciled: false }),
      ),
    )
    const user = userEvent.setup()
    renderDetail()
    await waitFor(() => screen.getByRole('button', { name: /release publicly/i }))
    await user.click(screen.getByRole('button', { name: /release publicly/i }))
    await user.click(screen.getByRole('button', { name: /confirm release/i }))
    await waitFor(() =>
      expect(screen.getByText(/released publicly/i)).toBeInTheDocument(),
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows error in modal when release endpoint returns 502', async () => {
    server.use(
      ...activeHandlers(),
      http.post(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/release-public`, () =>
        HttpResponse.json({ detail: 'YouTube API error' }, { status: 502 }),
      ),
    )
    const user = userEvent.setup()
    renderDetail()
    await waitFor(() => screen.getByRole('button', { name: /release publicly/i }))
    await user.click(screen.getByRole('button', { name: /release publicly/i }))
    await user.click(screen.getByRole('button', { name: /confirm release/i }))
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument())
    await waitFor(() =>
      expect(within(screen.getByRole('dialog')).getByText(/youtube api error/i)).toBeInTheDocument(),
    )
  })

  it('shows error in modal when release endpoint returns 403', async () => {
    server.use(
      ...activeHandlers(),
      http.post(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/release-public`, () =>
        HttpResponse.json({ detail: 'ACE_RELEASE_PUBLIC_ENABLED is not set' }, { status: 403 }),
      ),
    )
    const user = userEvent.setup()
    renderDetail()
    await waitFor(() => screen.getByRole('button', { name: /release publicly/i }))
    await user.click(screen.getByRole('button', { name: /release publicly/i }))
    await user.click(screen.getByRole('button', { name: /confirm release/i }))
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument())
    await waitFor(() =>
      expect(
        within(screen.getByRole('dialog')).getByText(/ACE_RELEASE_PUBLIC_ENABLED is not set/i),
      ).toBeInTheDocument(),
    )
  })

  it('prevents double-click during release (button disabled while releasing)', async () => {
    let resolveRelease: (() => void) | null = null
    server.use(
      ...activeHandlers(),
      http.post(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/release-public`, () =>
        new Promise<Response>(resolve => {
          resolveRelease = () =>
            resolve(HttpResponse.json({ visibility: 'public', reconciled: false }) as Response)
        }),
      ),
    )
    const user = userEvent.setup()
    renderDetail()
    await waitFor(() => screen.getByRole('button', { name: /release publicly/i }))
    await user.click(screen.getByRole('button', { name: /release publicly/i }))
    await user.click(screen.getByRole('button', { name: /confirm release/i }))
    // confirm button (inside dialog) is now disabled while releasing
    await waitFor(() => {
      const dialog = screen.getByRole('dialog')
      const confirmBtn = within(dialog).getByRole('button', { name: /releasing/i })
      expect(confirmBtn).toBeDisabled()
    })
    ;(resolveRelease as (() => void) | null)?.()
  })
})
