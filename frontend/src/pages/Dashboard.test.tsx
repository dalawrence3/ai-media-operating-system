/* Dashboard page — loading / populated / empty / degraded states */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { server } from '@/test/server'
import { Dashboard } from './Dashboard'
import { WS_ID, healthView, costView } from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

function renderDashboard(wsId = WS_ID) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workspaces/${wsId}/dashboard`]}>
        <Routes>
          <Route path="/workspaces/:workspaceId/dashboard" element={<Dashboard />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Dashboard', () => {
  describe('loading state', () => {
    it('shows a loading indicator while health is fetching', () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/health`, async () => {
          await new Promise(r => setTimeout(r, 300))
          return HttpResponse.json(healthView)
        }),
      )
      renderDashboard()
      expect(screen.getByText(/loading dashboard/i)).toBeInTheDocument()
    })
  })

  describe('populated state', () => {
    it('renders a health status badge after load', async () => {
      renderDashboard()
      // Health status appears as a badge after data loads
      await waitFor(() => expect(screen.queryByText(/loading/i)).not.toBeInTheDocument())
      // The overall_status is 'healthy' — StatusBadge renders "Healthy"
      expect(screen.getAllByText(/healthy/i).length).toBeGreaterThan(0)
    })

    it('shows dead letter tile with value 0', async () => {
      renderDashboard()
      await waitFor(() => screen.getByText('Dead Letters'))
      const tile = screen.getByText('Dead Letters').closest('.stat-tile')!
      expect(tile.textContent).toContain('0')
    })

    it('shows monthly spend from costs API', async () => {
      renderDashboard()
      await waitFor(() => screen.getByText('Monthly Spend'))
      const tile = screen.getByText('Monthly Spend').closest('.stat-tile')!
      expect(tile.textContent).toContain('12.34')
    })

    it('shows review pending count from review-queue', async () => {
      renderDashboard()
      await waitFor(() => screen.getByText('Reviews Pending'))
      // fixture has 1 review item
      const tile = screen.getByText('Reviews Pending').closest('.stat-tile')!
      expect(tile.textContent).toContain('1')
    })

    it('shows system health section', async () => {
      renderDashboard()
      await waitFor(() => screen.getByText('System Health'))
      expect(screen.getByText('System Health')).toBeInTheDocument()
    })
  })

  describe('degraded state', () => {
    it('shows error alert when health endpoint fails', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/health`, () =>
          HttpResponse.json({ detail: 'Internal error' }, { status: 500 }),
        ),
      )
      renderDashboard()
      await waitFor(() => screen.getByRole('alert'))
    })

    it('shows budget warning sub-label when cost is warning', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/costs`, () =>
          HttpResponse.json({ ...costView, warning_active: true }),
        ),
      )
      renderDashboard()
      await waitFor(() => screen.getByText('Monthly Spend'))
      expect(screen.getByText(/budget warning/i)).toBeInTheDocument()
    })

    it('shows dead letter count when > 0', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/health`, () =>
          HttpResponse.json({ ...healthView, dead_letter_count: 3 }),
        ),
      )
      renderDashboard()
      await waitFor(() => screen.getByText('Dead Letters'))
      const tile = screen.getByText('Dead Letters').closest('.stat-tile')!
      expect(tile.textContent).toContain('3')
    })
  })

  describe('empty workspace', () => {
    it('renders without crashing when all queues are empty', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/review-queue`, () => HttpResponse.json([])),
        http.get(`${B}/workspaces/${WS_ID}/exceptions`, () => HttpResponse.json([])),
        http.get(`${B}/workspaces/${WS_ID}/pipelines`, () => HttpResponse.json([])),
      )
      renderDashboard()
      await waitFor(() => screen.getByText('Dashboard'))
      expect(screen.queryByText('Pending Reviews')).not.toBeInTheDocument()
      expect(screen.queryByText('Active Exceptions')).not.toBeInTheDocument()
    })
  })
})
