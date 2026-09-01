/* Individual video analytics — Phase 17C. */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import { VideoAnalytics } from './VideoAnalytics'
import {
  WS_ID,
  PUB_ID,
  publicationDetail,
  publicationAnalytics,
  publicationAnalyticsHistory,
  recommendation,
} from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

function renderPage(workspaceId = WS_ID, pubId = String(PUB_ID)) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return {
    user: userEvent.setup(),
    ...render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/workspaces/${workspaceId}/analytics/${pubId}`]}>
          <Routes>
            <Route path="/workspaces/:workspaceId/analytics/:publicationId" element={<VideoAnalytics />} />
            <Route path="/workspaces/:workspaceId/analytics" element={<div data-testid="analytics-page" />} />
            <Route path="/workspaces/:workspaceId/content/:publicationId" element={<div data-testid="content-detail-page" />} />
            <Route path="/workspaces/:workspaceId/learn" element={<div data-testid="learn-page" />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  }
}

describe('VideoAnalytics', () => {
  it('shows the video title and lifecycle/visibility badges', async () => {
    renderPage()
    await waitFor(() => screen.getByText(publicationDetail.title))
    expect(screen.getByText('Private')).toBeInTheDocument()
    expect(screen.getByText('On YouTube')).toBeInTheDocument()
  })

  it('shows the latest performance metrics', async () => {
    renderPage()
    await waitFor(() => screen.getByText('Performance'))
    // publicationAnalytics fixture carries { views: 1234, ctr: 0.047 }
    expect(await screen.findByText('1.2K')).toBeInTheDocument()
  })

  it('renders a history chart when at least two observations report data', async () => {
    renderPage()
    await waitFor(() => screen.getByText('Performance over time'))
    // publicationAnalyticsHistory fixture has two 'data' snapshots sharing
    // the 'views' metric, so a metric selector with multiple options appears.
    await waitFor(() => screen.getByRole('group', { name: /history metric/i }))
    expect(screen.getByRole('button', { name: 'Views' })).toBeInTheDocument()
  })

  it('shows an honest insufficient-history message when fewer than two observations share a metric', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/analytics/history`, () =>
        HttpResponse.json([publicationAnalyticsHistory[0]]),
      ),
    )
    renderPage()
    await waitFor(() => screen.getByText(/not enough observation history yet/i))
  })

  it('shows an honest unavailable state for retention when no points exist', async () => {
    renderPage()
    await waitFor(() => screen.getByText('Retention'))
    expect(screen.getByText(/retention data not available/i)).toBeInTheDocument()
  })

  it('shows retention point count when retention data exists', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/analytics`, () =>
        HttpResponse.json({ ...publicationAnalytics, retention_point_count: 42 }),
      ),
    )
    renderPage()
    await waitFor(() => screen.getByText(/42 retention points recorded/i))
  })

  it('shows experiment linkage in the context card when present', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/analytics`, () =>
        HttpResponse.json({ ...publicationAnalytics, experiment_id: 'exp-123' }),
      ),
    )
    renderPage()
    await waitFor(() => screen.getByText('exp-123'))
  })

  it('does not show a context card when there is no experiment and no recommendations', async () => {
    renderPage()
    await waitFor(() => screen.getByText(publicationDetail.title))
    expect(screen.queryByText('Context')).not.toBeInTheDocument()
  })

  it('shows referencing learning signals and links to Learn', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/recommendations`, () => HttpResponse.json([recommendation])),
    )
    const { user } = renderPage()
    await waitFor(() => screen.getByText('Context'))
    expect(screen.getByText(recommendation.title)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /view in learn/i }))
    await waitFor(() => screen.getByTestId('learn-page'))
  })

  it('links back to the analytics landing page', async () => {
    const { user } = renderPage()
    await waitFor(() => screen.getByRole('button', { name: /analytics/i }))
    await user.click(screen.getByRole('button', { name: /^← analytics$/i }))
    await waitFor(() => screen.getByTestId('analytics-page'))
  })

  it('links to the content detail page to watch and manage the video', async () => {
    const { user } = renderPage()
    await waitFor(() => screen.getByRole('button', { name: /watch & manage/i }))
    await user.click(screen.getByRole('button', { name: /watch & manage/i }))
    await waitFor(() => screen.getByTestId('content-detail-page'))
  })

  it('shows a no-analytics-data empty state when the video has never been observed', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/analytics`, () =>
        HttpResponse.json({
          snapshot_id: null,
          snapshot_ingested_at: null,
          period_start: null,
          period_end: null,
          metrics: {},
          retention_point_count: 0,
          experiment_id: null,
        }),
      ),
      http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/analytics/history`, () =>
        HttpResponse.json([]),
      ),
    )
    renderPage()
    await waitFor(() => screen.getByText(/no analytics data yet/i))
  })

  it('keeps render and lineage metadata behind a technical-details disclosure', async () => {
    renderPage()
    await waitFor(() => screen.getByText('Render & lineage details'))
    const details = screen.getByText('Render & lineage details').closest('details')!
    expect(details).not.toHaveAttribute('open')
  })
})
