/* Analytics page — metric semantic labeling, empty/populated states */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import { Analytics } from './Analytics'
import { WS_ID, analyticsAggregate, analyticsAggregateCtr } from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

function renderAnalytics(wsId = WS_ID) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workspaces/${wsId}/analytics`]}>
        <Routes>
          <Route path="/workspaces/:workspaceId/analytics" element={<Analytics />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/** Return the method cell (2nd <td>) of the row whose first cell matches metricName */
function getMethodCell(metricName: string): HTMLElement {
  const cell = screen.getByText(metricName, { selector: 'td' })
  const row = cell.closest('tr')!
  return row.querySelectorAll('td')[1] as HTMLElement
}

describe('Analytics', () => {
  describe('empty state (no aggregates from API)', () => {
    it('shows explicit unavailable state (not a silent empty)', async () => {
      renderAnalytics()
      await waitFor(() => screen.getByText(/provider setup required/i))
    })

    it('does not show fake metric data', () => {
      renderAnalytics()
      expect(screen.queryByText(/1,234/)).not.toBeInTheDocument()
      expect(screen.queryByText(/views: \d/i)).not.toBeInTheDocument()
    })
  })

  describe('metric semantics table (always visible)', () => {
    it('labels CTR as latest_observation (gauge), not sum', async () => {
      renderAnalytics()
      await waitFor(() => screen.getByText('ctr', { selector: 'td' }))
      const method = getMethodCell('ctr')
      expect(method.textContent).toBe('latest_observation')
    })

    it('labels average_view_duration as latest_observation', async () => {
      renderAnalytics()
      await waitFor(() => screen.getByText('average_view_duration', { selector: 'td' }))
      const method = getMethodCell('average_view_duration')
      expect(method.textContent).toBe('latest_observation')
    })

    it('labels likes as latest_observation (current count, not incremental)', async () => {
      renderAnalytics()
      await waitFor(() => screen.getByText('likes', { selector: 'td' }))
      const method = getMethodCell('likes')
      expect(method.textContent).toBe('latest_observation')
    })

    it('labels views as sum (additive)', async () => {
      renderAnalytics()
      await waitFor(() => screen.getByText('views', { selector: 'td' }))
      const method = getMethodCell('views')
      expect(method.textContent).toBe('sum')
    })

    it('no gauge metric method cell contains "sum"', async () => {
      renderAnalytics()
      await waitFor(() => screen.getByText('ctr', { selector: 'td' }))
      for (const metric of ['ctr', 'average_view_duration', 'likes']) {
        const method = getMethodCell(metric)
        expect(method.textContent).not.toBe('sum')
      }
    })

    it('notes that revenue requires currency and mixed currencies are not combined', async () => {
      renderAnalytics()
      await waitFor(() => screen.getByText('revenue_estimate', { selector: 'td' }))
      const row = screen.getByText('revenue_estimate', { selector: 'td' }).closest('tr')!
      expect(row.textContent).toContain('Currency')
    })
  })

  describe('populated state (aggregates returned from API)', () => {
    it('shows aggregate rows when data is available', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/analytics/aggregates`, () =>
          HttpResponse.json([analyticsAggregate, analyticsAggregateCtr]),
        ),
      )
      renderAnalytics()
      await waitFor(() => screen.getByText('lifetime', { selector: 'h2' }))
      // Both the aggregates table and the metric semantics table contain 'views' —
      // use getAllByText and assert at least one td with that text.
      const viewsCells = screen.getAllByText('views', { selector: 'td' })
      expect(viewsCells.length).toBeGreaterThanOrEqual(1)
    })

    it('does not show unavailable state when aggregates exist', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/analytics/aggregates`, () =>
          HttpResponse.json([analyticsAggregate]),
        ),
      )
      renderAnalytics()
      await waitFor(() => screen.getByText('lifetime', { selector: 'h2' }))
      expect(screen.queryByText(/provider setup required/i)).not.toBeInTheDocument()
    })

    it('displays metric value for views aggregate', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/analytics/aggregates`, () =>
          HttpResponse.json([analyticsAggregate]),
        ),
      )
      renderAnalytics()
      await waitFor(() => screen.getByText('42.0K'))
    })

    it('still shows metric semantics table when data is loaded', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/analytics/aggregates`, () =>
          HttpResponse.json([analyticsAggregate]),
        ),
      )
      renderAnalytics()
      await waitFor(() => screen.getByText('lifetime', { selector: 'h2' }))
      expect(screen.getByText('Metric Semantics')).toBeInTheDocument()
    })
  })
})
