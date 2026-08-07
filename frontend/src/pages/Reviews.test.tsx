/* Reviews page — queue rendering, approve, reject, authorization failure */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { server } from '@/test/server'
import { Reviews } from './Reviews'
import { WS_ID, reviewItem, reviewItem2 } from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

function renderReviews(wsId = WS_ID) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return {
    user: userEvent.setup(),
    ...render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/workspaces/${wsId}/reviews`]}>
          <Routes>
            <Route path="/workspaces/:workspaceId/reviews" element={<Reviews />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  }
}

describe('Reviews', () => {
  describe('queue rendering', () => {
    it('shows review item description', async () => {
      renderReviews()
      await waitFor(() => screen.getByText(/How AI Works/))
      expect(screen.getByText(/How AI Works/)).toBeInTheDocument()
    })

    it('shows item type tag in the table', async () => {
      renderReviews()
      await waitFor(() => screen.getByText(/How AI Works/))
      // 'script' appears in the table tag (span.tag) AND the filter option — use getAllByText
      const items = screen.getAllByText('script')
      expect(items.length).toBeGreaterThan(0)
    })

    it('shows empty state when queue is empty', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/review-queue`, () => HttpResponse.json([])),
      )
      renderReviews()
      await waitFor(() => screen.getByText(/no pending reviews/i))
    })

    it('shows error alert when queue fails to load', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/review-queue`, () =>
          HttpResponse.json({ detail: 'DB error' }, { status: 500 }),
        ),
      )
      renderReviews()
      await waitFor(() => screen.getByRole('alert'))
    })

    it('shows type filter when multiple types exist', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/review-queue`, () =>
          HttpResponse.json([reviewItem, reviewItem2]),
        ),
      )
      renderReviews()
      await waitFor(() => screen.getByRole('combobox', { name: /filter by type/i }))
    })
  })

  describe('approve flow', () => {
    it('shows approve button when item is selected', async () => {
      const { user } = renderReviews()
      await waitFor(() => screen.getByText(/How AI Works/))
      // Click the review row
      // Review row has role="button" — accessible name comes from cell text content
      const row = screen.getByRole('button', { name: /How AI Works/i })
      await user.click(row)
      expect(await screen.findByRole('button', { name: /approve/i })).toBeInTheDocument()
    })

    it('calls approve endpoint on approve click', async () => {
      let approveCallCount = 0
      server.use(
        http.post(`${B}/workspaces/${WS_ID}/reviews/:type/:id/approve`, () => {
          approveCallCount++
          return HttpResponse.json({ ok: true })
        }),
      )
      const { user } = renderReviews()
      await waitFor(() => screen.getByText(/How AI Works/))
      const row = screen.getByRole('button', { name: /How AI Works/i })
      await user.click(row)
      const approveBtn = await screen.findByRole('button', { name: /approve/i })
      await user.click(approveBtn)
      await waitFor(() => expect(approveCallCount).toBe(1))
    })
  })

  describe('reject flow', () => {
    async function openRejectModal(user: ReturnType<typeof userEvent.setup>) {
      await waitFor(() => screen.getByText(/How AI Works/))
      const row = screen.getByRole('button', { name: /How AI Works/i })
      await user.click(row)
      const rejectBtn = await screen.findByRole('button', { name: /reject/i })
      await user.click(rejectBtn)
      await screen.findByRole('dialog')
    }

    it('shows reject modal requiring a reason', async () => {
      const { user } = renderReviews()
      await openRejectModal(user)
      expect(screen.getByLabelText(/reason \(required\)/i)).toBeInTheDocument()
    })

    it('confirm reject is disabled until reason is provided', async () => {
      const { user } = renderReviews()
      await openRejectModal(user)
      expect(screen.getByRole('button', { name: /confirm reject/i })).toBeDisabled()
    })

    it('confirm reject is enabled after reason is typed', async () => {
      const { user } = renderReviews()
      await openRejectModal(user)
      await user.type(screen.getByLabelText(/reason \(required\)/i), 'Content not suitable')
      expect(screen.getByRole('button', { name: /confirm reject/i })).not.toBeDisabled()
    })
  })

  describe('authorization failure', () => {
    it('shows error message when approve returns 403', async () => {
      server.use(
        http.post(`${B}/workspaces/${WS_ID}/reviews/:type/:id/approve`, () =>
          HttpResponse.json({ detail: 'Actor not authorized' }, { status: 403 }),
        ),
      )
      const { user } = renderReviews()
      await waitFor(() => screen.getByText(/How AI Works/))
      const row = screen.getByRole('button', { name: /How AI Works/i })
      await user.click(row)
      const approveBtn = await screen.findByRole('button', { name: /approve/i })
      await user.click(approveBtn)
      await waitFor(() => screen.getByText(/Actor not authorized/))
    })
  })
})
