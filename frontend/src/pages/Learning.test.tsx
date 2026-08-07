/* Learning page — observational semantics, confidence model, accept/reject labeling */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Learning } from './Learning'
import { WS_ID } from '@/test/fixtures'

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
  describe('unavailable state', () => {
    it('shows explicit "no analytics data" unavailable state', async () => {
      renderLearning()
      await waitFor(() => screen.getByText(/no analytics data yet/i))
    })

    it('does not show fake recommendation data', () => {
      renderLearning()
      expect(screen.queryByText(/confidence: 0\.\d/)).not.toBeInTheDocument()
    })
  })

  describe('confidence and evidence semantics', () => {
    it('labels confidence as heuristic signal strength — not statistical confidence', async () => {
      renderLearning()
      await waitFor(() => screen.getByText('Confidence Score'))
      const tile = screen.getByText('Confidence Score').closest('div')!
      expect(tile.textContent).toContain('heuristic signal strength')
      expect(tile.textContent).toContain('NOT a statistical confidence interval')
    })

    it('describes recommendations as observational, not causal', async () => {
      renderLearning()
      // The key disclaimer must be visible: associations, not causes
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
      // The word "causes" is allowed in "not causes" (a denial of causation)
      // What is NOT allowed is a positive causal claim like "X causes Y" or "causes improvement"
      expect(body).not.toMatch(/\bcauses\s+(?!not\b)\w+/i)
    })
  })
})
