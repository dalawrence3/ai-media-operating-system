/* Learning page — observational semantics, confidence model, accept/reject labeling */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import { Learning } from './Learning'
import { WS_ID, recommendation, recommendationAccepted } from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

function renderLearning(wsId = WS_ID) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workspaces/${wsId}/learning`]}>
        <Routes>
          <Route path="/workspaces/:workspaceId/learning" element={<Learning />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Learning', () => {
  describe('empty state (no recommendations from API)', () => {
    it('shows explicit "no analytics data" unavailable state', async () => {
      renderLearning()
      await waitFor(() => screen.getByText(/no analytics data yet/i))
    })

    it('does not show fake recommendation data', () => {
      renderLearning()
      expect(screen.queryByText(/confidence: 0\.\d/)).not.toBeInTheDocument()
    })
  })

  describe('confidence and evidence semantics (always visible)', () => {
    it('labels confidence as heuristic signal strength — not statistical confidence', async () => {
      renderLearning()
      await waitFor(() => screen.getByText('Confidence Score'))
      const tile = screen.getByText('Confidence Score').closest('div')!
      expect(tile.textContent).toContain('heuristic signal strength')
      expect(tile.textContent).toContain('NOT a statistical confidence interval')
    })

    it('describes recommendations as observational, not causal', async () => {
      renderLearning()
      await waitFor(() => screen.getByText(/they describe associations, not causes/i))
    })

    it('uses observational framing in the evidence classification section', async () => {
      renderLearning()
      await waitFor(() => screen.getByText('Evidence Classification'))
      const tile = screen.getByText('Evidence Classification').closest('div')!
      expect(tile.textContent).toContain('observational')
    })

    it('explains exploratory vs actionable thresholds', async () => {
      renderLearning()
      await waitFor(() => screen.getByText('Exploratory'))
      expect(screen.getByText('Actionable')).toBeInTheDocument()
    })

    it('states that accepting does NOT auto-apply changes', async () => {
      renderLearning()
      await waitFor(() => screen.getByText('Accept / Reject'))
      const tile = screen.getByText('Accept / Reject').closest('div')!
      expect(tile.textContent).toContain('does not modify')
    })

    it('page body does not make bare causal claims ("X causes Y")', async () => {
      renderLearning()
      await waitFor(() => screen.getByText('Learning'))
      const body = document.body.textContent ?? ''
      expect(body).not.toMatch(/\bcauses\s+(?!not\b)\w+/i)
    })
  })

  describe('populated state (recommendations returned from API)', () => {
    it('shows recommendation title when data is available', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/recommendations`, () =>
          HttpResponse.json([recommendation]),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText(recommendation.title))
    })

    it('shows confidence score for each recommendation', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/recommendations`, () =>
          HttpResponse.json([recommendation]),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText('62%'))
    })

    it('does not show unavailable state when recommendations exist', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/recommendations`, () =>
          HttpResponse.json([recommendation]),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText(recommendation.title))
      expect(screen.queryByText(/no analytics data yet/i)).not.toBeInTheDocument()
    })

    it('shows accept button for pending recommendations', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/recommendations`, () =>
          HttpResponse.json([recommendation]),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByTestId(`accept-${recommendation.id}`))
    })

    it('shows reject button that expands notes textarea', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/recommendations`, () =>
          HttpResponse.json([recommendation]),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByTestId(`reject-open-${recommendation.id}`))
      fireEvent.click(screen.getByTestId(`reject-open-${recommendation.id}`))
      await waitFor(() => screen.getByTestId(`reject-notes-${recommendation.id}`))
    })

    it('shows accepted status badge for accepted recommendation', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/recommendations`, () =>
          HttpResponse.json([recommendationAccepted]),
        ),
      )
      renderLearning()
      // Wait for the specific card — the filter bar also renders 'accepted' text
      // when hasData=true, so use within() to check the badge inside the card.
      await waitFor(() =>
        screen.getByTestId(`recommendation-${recommendationAccepted.id}`),
      )
      const card = screen.getByTestId(`recommendation-${recommendationAccepted.id}`)
      expect(within(card).getByText('accepted')).toBeInTheDocument()
    })

    it('still shows confidence model section when data is loaded', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/recommendations`, () =>
          HttpResponse.json([recommendation]),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText(recommendation.title))
      expect(screen.getByText('Confidence & Evidence Model')).toBeInTheDocument()
    })
  })
})
