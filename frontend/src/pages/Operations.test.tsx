/* Operations Center — lifecycle states, correlation, actions */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { server } from '@/test/server'
import { Operations } from './Operations'
import { WS_ID, operationView } from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

function renderOperations(wsId = WS_ID) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workspaces/${wsId}/operations`]}>
        <Routes>
          <Route path="/workspaces/:workspaceId/operations" element={<Operations />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Operations', () => {
  it('renders operation type tag', async () => {
    renderOperations()
    await waitFor(() => screen.getByText('publish_video'))
  })

  it('renders operation status badge', async () => {
    renderOperations()
    await waitFor(() => screen.getByText('Completed'))
  })

  it('renders actor', async () => {
    renderOperations()
    await waitFor(() => screen.getByText('dev:studio-user'))
  })

  it('renders correlation id', async () => {
    renderOperations()
    await waitFor(() => screen.getByText('corr-001'))
  })

  it('does not show retry button for completed operations', async () => {
    renderOperations()
    await waitFor(() => screen.getByText('Completed'))
    expect(screen.queryByRole('button', { name: /retry operation/i })).not.toBeInTheDocument()

  })

  it('shows retry button for failed operations', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/operations`, () =>
        HttpResponse.json([{ ...operationView, status: 'failed', error_message: 'timeout' }]),
      ),
    )
    renderOperations()
    // waitFor the Retry button — aria-label is "Retry operation <id>", only appears when status === 'failed'
    await waitFor(() => screen.getByRole('button', { name: /retry operation/i }), { timeout: 3000 })
  })

  it('shows empty state when no operations', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/operations`, () => HttpResponse.json([])),
    )
    renderOperations()
    // Use exact title string — the description "No operations found." is a separate element
    await waitFor(() => screen.getByText('No operations'))
  })
})
