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
  publicationVisualQuality,
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
  http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/visual-quality`, () =>
    HttpResponse.json(publicationVisualQuality),
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
      <MemoryRouter initialEntries={[`/workspaces/${workspaceId}/content/${pubId}`]}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/content/:publicationId"
            element={<PublicationDetail />}
          />
          <Route
            path="/workspaces/:workspaceId/content"
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

  it('shows visibility and status as badges', async () => {
    renderDetail()
    await waitFor(() => {
      expect(screen.getByText('Private')).toBeInTheDocument()
      expect(screen.getByText('On YouTube')).toBeInTheDocument()
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

  it('shows the topic as the page subtitle', async () => {
    renderDetail()
    await waitFor(() => screen.getByText(publicationDetail.topic_title!))
  })

  it('formats the publish date via local time, not a raw ISO string', async () => {
    renderDetail()
    await waitFor(() => screen.getByText('Publish date'))
    const row = screen.getByText('Publish date').closest('.detail-meta-row') as HTMLElement
    const time = row.querySelector('time')!
    expect(time.getAttribute('dateTime')).toBe(publicationDetail.published_at)
    expect(time.textContent).not.toContain('T')
  })

  it('keeps render and lineage metadata collapsed behind a technical-details disclosure', async () => {
    renderDetail()
    await waitFor(() => screen.getByText('Render & lineage details'))
    const details = screen.getByText('Render & lineage details').closest('details')!
    expect(details).not.toHaveAttribute('open')
    // The resolution field lives inside the disclosure, not the always-visible sidebar.
    expect(details.querySelector('.detail-meta-list')).not.toBeNull()
  })

  it('reveals render metadata once the technical-details disclosure is opened', async () => {
    const user = userEvent.setup()
    renderDetail()
    await waitFor(() => screen.getByText('Render & lineage details'))
    await user.click(screen.getByText('Render & lineage details'))
    expect(screen.getByText('1080×1920')).toBeInTheDocument()
  })

  it('links to the dedicated analytics view for this video', async () => {
    renderDetail()
    await waitFor(() => screen.getByRole('button', { name: /view analytics/i }))
    expect(screen.getByRole('button', { name: /view analytics/i })).toBeInTheDocument()
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

// ── Phase 18E — visual quality ────────────────────────────────────────────────

describe('PublicationDetail — visual quality', () => {
  it('shows the verdict and the numbers behind it', async () => {
    renderDetail()

    const panel = await screen.findByTestId('visual-quality-panel')
    expect(within(panel).getByTestId('visual-quality-status')).toHaveTextContent('Blocked')

    // The operator must be able to read WHY, not just THAT.
    expect(
      within(panel).getByText(/Only 16% of runtime carries a meaningful visual/),
    ).toBeInTheDocument()
    expect(within(panel).getByText('16%')).toBeInTheDocument()
    expect(within(panel).getByText('84%')).toBeInTheDocument()
    expect(within(panel).getByText('50.3s')).toBeInTheDocument()
  })

  it('separates retrieval failures from deliberate fallbacks', async () => {
    renderDetail()
    const panel = await screen.findByTestId('visual-quality-panel')

    expect(within(panel).getByText('Retrieval fallbacks')).toBeInTheDocument()
    expect(within(panel).getByText('15')).toBeInTheDocument()
    expect(within(panel).getByText('3 by design')).toBeInTheDocument()
  })

  it('keeps the per-scene planned-vs-realized breakdown one click away', async () => {
    const user = userEvent.setup()
    renderDetail()

    const toggle = await screen.findByTestId('toggle-visual-scenes')
    expect(screen.queryAllByTestId('visual-scene-row')).toHaveLength(0)

    await user.click(toggle)

    const rows = await screen.findAllByTestId('visual-scene-row')
    expect(rows).toHaveLength(2)
    // Beat 1 wanted footage and got a text card because retrieval failed.
    expect(within(rows[1]).getByText('Motion footage')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Text card')).toBeInTheDocument()
    expect(within(rows[1]).getByText('all_candidates_rejected')).toBeInTheDocument()
    // Beat 0 fell back on purpose and is not presented as a fault.
    expect(within(rows[0]).getByText('by design')).toBeInTheDocument()
  })

  it('says so plainly when a video predates visual assessment', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/visual-quality`, () =>
        HttpResponse.json({ assessed: false }),
      ),
    )
    renderDetail()

    expect(
      await screen.findByText(/produced before visual quality assessment existed/),
    ).toBeInTheDocument()
  })
})
