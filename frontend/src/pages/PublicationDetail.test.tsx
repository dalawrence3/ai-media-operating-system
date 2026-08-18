import { describe, it, expect, vi, beforeAll, afterEach, afterAll } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'
import { PublicationDetail } from './PublicationDetail'
import { WS_ID, PUB_ID, publicationDetail, publicationAnalytics } from '@/test/fixtures'

// Stub URL.createObjectURL / revokeObjectURL (not available in jsdom)
globalThis.URL.createObjectURL = vi.fn(() => 'blob:fake-video-url')
globalThis.URL.revokeObjectURL = vi.fn()

const B = 'http://localhost:5173/api/v1'

const server = setupServer(
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
)

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function renderDetail(workspaceId = WS_ID, publicationId = String(PUB_ID)) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workspaces/${workspaceId}/publishing/${publicationId}`]}>
        <Routes>
          <Route path="/workspaces/:workspaceId/publishing/:publicationId" element={<PublicationDetail />} />
          <Route path="/workspaces/:workspaceId/publishing" element={<div data-testid="list-page" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('PublicationDetail page', () => {
  it('renders the publication title', async () => {
    renderDetail()
    await waitFor(() => {
      expect(screen.getByText('Why Renewable Energy Is Getting So Cheap')).toBeInTheDocument()
    })
  })

  it('renders the video element with blob URL', async () => {
    renderDetail()
    await waitFor(() => {
      const video = screen.getByTestId('publication-video') as HTMLVideoElement
      expect(video).toBeInTheDocument()
      expect(video.src).toContain('blob:fake-video-url')
    })
  })

  it('shows render metadata in sidebar', async () => {
    renderDetail()
    await waitFor(() => {
      expect(screen.getByText('1080×1920')).toBeInTheDocument()
      expect(screen.getByText('0m 59s')).toBeInTheDocument()
    })
  })

  it('shows analytics metrics', async () => {
    renderDetail()
    await waitFor(() => {
      expect(screen.getByText('4.7%')).toBeInTheDocument()  // ctr formatted
    })
  })

  it('shows Release Publicly button as disabled', async () => {
    renderDetail()
    await waitFor(() => screen.getByText('Why Renewable Energy Is Getting So Cheap'))
    const btn = screen.getByRole('button', { name: /release publicly/i })
    expect(btn).toBeDisabled()
  })

  it('shows tags', async () => {
    renderDetail()
    await waitFor(() => {
      expect(screen.getByText('#energy')).toBeInTheDocument()
    })
  })

  it('shows error state when video unavailable', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/stream`, () =>
        HttpResponse.json({ detail: 'Render not available' }, { status: 404 }),
      ),
    )
    renderDetail()
    await waitFor(() => {
      expect(screen.getByText(/video unavailable/i)).toBeInTheDocument()
    })
  })

  it('shows no analytics message when snapshot is null', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/analytics`, () =>
        HttpResponse.json({
          snapshot_id: null,
          snapshot_ingested_at: null,
          period_start: null,
          period_end: null,
          metrics: {},
          retention_point_count: 0,
        }),
      ),
    )
    renderDetail()
    await waitFor(() => {
      expect(screen.getByText(/no analytics data yet/i)).toBeInTheDocument()
    })
  })
})
