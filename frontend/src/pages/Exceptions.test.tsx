/* Exception Center — category rendering, severity, empty state */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { server } from '@/test/server'
import { Exceptions } from './Exceptions'
import { WS_ID, exceptionView } from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

function renderExceptions(wsId = WS_ID) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workspaces/${wsId}/exceptions`]}>
        <Routes>
          <Route path="/workspaces/:workspaceId/exceptions" element={<Exceptions />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Exceptions', () => {
  it('renders exception type tag', async () => {
    renderExceptions()
    await waitFor(() => screen.getByText('budget_warning'))
  })

  it('renders exception severity badge', async () => {
    renderExceptions()
    // severity: 'warn' → StatusBadge maps to label 'Warn'
    await waitFor(() => screen.getByText('Warn'))
  })

  it('renders exception description', async () => {
    renderExceptions()
    await waitFor(() => screen.getByText(exceptionView.description))
  })

  it('renders entity ID', async () => {
    renderExceptions()
    // entity_id is WS_ID which appears in the card
    await waitFor(() => {
      const entityLabel = screen.getByText('Entity ID')
      const card = entityLabel.closest('div')!
      expect(card.textContent).toContain(exceptionView.entity_id)
    })
  })

  it('shows empty state when no exceptions', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/exceptions`, () => HttpResponse.json([])),
    )
    renderExceptions()
    await waitFor(() => screen.getByText(/no active exceptions/i))
  })

  it('shows error alert on load failure', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/exceptions`, () =>
        HttpResponse.json({ detail: 'error' }, { status: 500 }),
      ),
    )
    renderExceptions()
    await waitFor(() => screen.getByRole('alert'))
  })
})
