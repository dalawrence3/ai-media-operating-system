/* Environment readiness page — Phase 16. */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import { Environment } from './Environment'
import { WS_ID } from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

function renderPage(workspaceId = WS_ID) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workspaces/${workspaceId}/environment`]}>
        <Routes>
          <Route path="/workspaces/:workspaceId/environment" element={<Environment />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function renderWithoutWorkspace() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/no-workspace']}>
        <Routes>
          <Route path="/no-workspace" element={<Environment />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const readyResponse = {
  analytics_ready: true,
  pilot_ready: false,
  checks: {
    market_intelligence_configured: { ok: true, detail: 'YouTube Data API key configured' },
    recurring_market_refresh_active: { ok: false, detail: 'no schedule found' },
  },
}

describe('Environment', () => {
  it('shows a loading state before data arrives', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/environment`, async () => {
        await new Promise(resolve => setTimeout(resolve, 50))
        return HttpResponse.json(readyResponse)
      }),
    )
    renderPage()
    expect(screen.getByText('Checking environment…')).toBeInTheDocument()
    await waitFor(() => screen.getByText('Pilot Gates'))
  })

  it('renders pilot gates reflecting ready and not-ready state', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/environment`, () => HttpResponse.json(readyResponse)),
    )
    renderPage()
    await waitFor(() => screen.getByText('Pilot Gates'))
    expect(screen.getByText('Analytics Ready')).toBeInTheDocument()
    expect(screen.getByText('Pilot Ready')).toBeInTheDocument()
    expect(screen.getAllByText('Prerequisites met')).toHaveLength(1)
    expect(screen.getAllByText('Prerequisites not met')).toHaveLength(1)
  })

  it('renders one row per prerequisite check with its ok/missing status', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/environment`, () => HttpResponse.json(readyResponse)),
    )
    renderPage()
    await waitFor(() => screen.getByText('Prerequisite Checks'))
    expect(screen.getByText('market_intelligence_configured')).toBeInTheDocument()
    expect(screen.getByText('YouTube Data API key configured')).toBeInTheDocument()
    expect(screen.getByText('recurring_market_refresh_active')).toBeInTheDocument()
    expect(screen.getByText('no schedule found')).toBeInTheDocument()
    expect(screen.getByText('ok')).toBeInTheDocument()
    expect(screen.getByText('missing')).toBeInTheDocument()
  })

  it('shows an error state when the environment endpoint fails', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/environment`, () => HttpResponse.error()),
    )
    renderPage()
    await waitFor(() => screen.getByText('Could not load environment status'))
  })

  it('shows an unavailable state when no workspace is selected', () => {
    renderWithoutWorkspace()
    expect(screen.getByText('No workspace selected')).toBeInTheDocument()
  })
})
