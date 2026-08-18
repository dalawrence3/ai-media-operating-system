import { describe, it, expect, vi, beforeAll, afterEach, afterAll } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'
import { Publishing } from './Publishing'
import { WS_ID, publicationListItem, PUB_ID } from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

const server = setupServer(
  http.get(`${B}/workspaces/${WS_ID}/publications`, () =>
    HttpResponse.json([publicationListItem]),
  ),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function renderPublishing(workspaceId = WS_ID) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workspaces/${workspaceId}/publishing`]}>
        <Routes>
          <Route path="/workspaces/:workspaceId/publishing" element={<Publishing />} />
          <Route path="/workspaces/:workspaceId/publishing/:publicationId" element={<div data-testid="detail-page" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Publishing list page', () => {
  it('renders the page title', async () => {
    renderPublishing()
    expect(screen.getByText('Publishing')).toBeInTheDocument()
  })

  it('shows publication title after loading', async () => {
    renderPublishing()
    await waitFor(() => {
      expect(screen.getByText('Why Renewable Energy Is Getting So Cheap')).toBeInTheDocument()
    })
  })

  it('shows visibility and status', async () => {
    renderPublishing()
    await waitFor(() => {
      expect(screen.getByText('private')).toBeInTheDocument()
      expect(screen.getByText('published')).toBeInTheDocument()
    })
  })

  it('navigates to detail on row click', async () => {
    renderPublishing()
    await waitFor(() => screen.getByText('Why Renewable Energy Is Getting So Cheap'))
    await userEvent.click(screen.getByText('Why Renewable Energy Is Getting So Cheap'))
    await waitFor(() => {
      expect(screen.getByTestId('detail-page')).toBeInTheDocument()
    })
  })

  it('shows empty state when no publications', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/publications`, () => HttpResponse.json([])),
    )
    renderPublishing()
    await waitFor(() => {
      expect(screen.getByText(/no publications yet/i)).toBeInTheDocument()
    })
  })
})
