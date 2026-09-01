/* Analytics page — Phase 17C channel performance and video-by-video view. */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import { Analytics } from './Analytics'
import { WS_ID, publicationListItem, publicationAnalytics, cpAccount1 } from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

function renderAnalytics(wsId = WS_ID) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return {
    user: userEvent.setup(),
    ...render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/workspaces/${wsId}/analytics`]}>
          <Routes>
            <Route path="/workspaces/:workspaceId/analytics" element={<Analytics />} />
            <Route path="/workspaces/:workspaceId/analytics/:publicationId" element={<div data-testid="video-analytics-page" />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  }
}

describe('Analytics', () => {
  describe('empty state (no publications at all)', () => {
    it('shows an explicit empty state rather than a blank page', async () => {
      server.use(http.get(`${B}/workspaces/${WS_ID}/publications`, () => HttpResponse.json([])))
      renderAnalytics()
      await waitFor(() => screen.getByText(/no videos yet/i))
    })
  })

  describe('populated state', () => {
    it('shows channel-level KPI cards computed from per-publication analytics', async () => {
      renderAnalytics()
      // "Views" also labels the metric-toggle button in the chart section
      // below, so scope the assertion to the KPI tile's own label class.
      await waitFor(() => screen.getByText('Views', { selector: '.metric-card-label' }))
      expect(screen.getByText('Watch time', { selector: '.metric-card-label' })).toBeInTheDocument()
      expect(screen.getByText('Avg % viewed', { selector: '.metric-card-label' })).toBeInTheDocument()
      expect(screen.getByText('Engaged views', { selector: '.metric-card-label' })).toBeInTheDocument()
      expect(screen.getByText('Likes', { selector: '.metric-card-label' })).toBeInTheDocument()
      expect(screen.getByText('Subscribers gained', { selector: '.metric-card-label' })).toBeInTheDocument()
    })

    it('shows the channel identity in the subtitle when a primary account exists', async () => {
      renderAnalytics()
      await waitFor(() => screen.getByText(new RegExp(cpAccount1.display_name)))
    })

    it('shows the performance-over-time section with a metric selector', async () => {
      renderAnalytics()
      await waitFor(() => screen.getByText('Performance over time'))
      expect(screen.getByRole('group', { name: /metric/i })).toBeInTheDocument()
    })

    it('shows a video card for each publication', async () => {
      renderAnalytics()
      await waitFor(() => screen.getByText('Video performance'))
      expect(screen.getByText(publicationListItem.title)).toBeInTheDocument()
    })

    it('navigates to the per-video analytics route when a video card is clicked', async () => {
      const { user } = renderAnalytics()
      await waitFor(() => screen.getByText(publicationListItem.title))
      await user.click(screen.getByText(publicationListItem.title))
      await waitFor(() => screen.getByTestId('video-analytics-page'))
    })

    it('offers sort controls for the video grid', async () => {
      renderAnalytics()
      await waitFor(() => screen.getByRole('group', { name: /sort videos/i }))
      expect(screen.getByRole('button', { name: 'Most viewed' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Most watch time' })).toBeInTheDocument()
    })

    it('does not silently drop a publication scoped to a topic with no workspace_id', async () => {
      // Publication 3's real-world analogue: topic_id has a NULL workspace_id,
      // which breaks the workspace-wide aggregates endpoint (Phase 17B/17C
      // finding). This page must never call that endpoint — it fans out
      // per-publication instead — so a second publication always renders.
      const secondPub = { ...publicationListItem, id: 3, title: 'Second Video' }
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/publications`, () =>
          HttpResponse.json([publicationListItem, secondPub]),
        ),
        http.get(`${B}/workspaces/${WS_ID}/publications/3/analytics`, () =>
          HttpResponse.json({ ...publicationAnalytics, snapshot_id: 2 }),
        ),
      )
      renderAnalytics()
      await waitFor(() => screen.getByText('Second Video'))
      expect(screen.getByText(publicationListItem.title)).toBeInTheDocument()
    })
  })
})
