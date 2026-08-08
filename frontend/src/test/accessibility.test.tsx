/* Accessibility — keyboard usability, labeled controls, status not color-only */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { server } from '@/test/server'
import { Reviews } from '@/pages/Reviews'
import { Pipelines } from '@/pages/Pipelines'
import { StatusBadge } from '@/components/common/StatusBadge'
import { WS_ID } from './fixtures'

const B = 'http://localhost:5173/api/v1'

function wrap(element: React.ReactElement, wsId = WS_ID, path = 'reviews') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return {
    user: userEvent.setup(),
    ...render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/workspaces/${wsId}/${path}`]}>
          <Routes>
            <Route path={`/workspaces/:workspaceId/${path}`} element={element} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  }
}

describe('Accessibility', () => {
  describe('StatusBadge — status not conveyed by color alone', () => {
    it('has aria-label attribute conveying the status text', () => {
      render(<StatusBadge status="failed" />)
      expect(screen.getByRole('generic', { name: /Status: Failed/i })).toBeInTheDocument()
    })

    it('has visible text label alongside any color indicator', () => {
      render(<StatusBadge status="blocked" />)
      expect(screen.getByText('Blocked')).toBeVisible()
    })
  })

  describe('Reviews — keyboard navigation', () => {
    it('review rows have tabIndex=0 for keyboard focus', async () => {
      wrap(<Reviews />)
      await waitFor(() => screen.getByText(/How AI Works/))
      // The review row has role="button" and tabIndex=0
      const row = screen.getByRole('button', { name: /How AI Works/i })
      expect(row).toHaveAttribute('tabindex', '0')
    })

    it('type filter select has an accessible label when multiple types exist', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/review-queue`, () =>
          HttpResponse.json([
            {
              item_type: 'script',
              item_id: 'id-001',
              workspace_id: WS_ID,
              channel_id: null,
              description: 'Item A',
              status: 'pending',
              created_at: '2025-01-01T00:00:00',
              metadata: {},
            },
            {
              item_type: 'narration',
              item_id: 'id-002',
              workspace_id: WS_ID,
              channel_id: null,
              description: 'Item B',
              status: 'pending',
              created_at: '2025-01-01T00:00:00',
              metadata: {},
            },
          ]),
        ),
      )
      wrap(<Reviews />)
      await waitFor(() => screen.getByRole('combobox', { name: /filter by type/i }))
    })
  })

  describe('Pipelines — keyboard usability', () => {
    it('pipeline list buttons have accessible aria-label', async () => {
      wrap(<Pipelines />, WS_ID, 'pipelines')
      // aria-label is "Pipeline {id.slice(0,8)}" = "Pipeline pipe-tes" for 'pipe-test-001'
      await waitFor(() => screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    })

    it('status filter select has an accessible label', async () => {
      wrap(<Pipelines />, WS_ID, 'pipelines')
      await waitFor(() => screen.getByRole('combobox', { name: /filter by status/i }))
    })
  })

  describe('Reviews reject modal — form accessibility', () => {
    async function openRejectModal(user: ReturnType<typeof userEvent.setup>) {
      await waitFor(() => screen.getByText(/How AI Works/))
      await user.click(screen.getByRole('button', { name: /How AI Works/i }))
      await user.click(await screen.findByRole('button', { name: /reject/i }))
      await screen.findByRole('dialog')
    }

    it('reject reason input has a visible label', async () => {
      const { user } = wrap(<Reviews />)
      await openRejectModal(user)
      expect(screen.getByLabelText(/reason \(required\)/i)).toBeInTheDocument()
    })

    it('notes textarea has a visible label', async () => {
      const { user } = wrap(<Reviews />)
      await openRejectModal(user)
      expect(screen.getByLabelText(/notes \(optional\)/i)).toBeInTheDocument()
    })
  })
})
