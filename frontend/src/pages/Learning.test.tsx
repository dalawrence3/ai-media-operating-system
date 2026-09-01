/* Learning page — Phase 17D decision-support layer.
   Covers: learning summary, channel evidence, recommendations (accept/reject
   unchanged), creative factors, market intelligence, experiment proposals,
   and the observational/causal-language safety semantics carried forward
   from the original M14.6 recommendations UI. */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import { Learning } from './Learning'
import {
  WS_ID,
  CH_ID,
  PUB_ID,
  recommendation,
  recommendationAccepted,
  marketOpportunity,
  experimentStrategyBrief,
  channelPerformanceBaseline,
  featurePerformanceObservation,
  publicationListItem,
  publicationAnalytics,
  opportunityEvidenceResponse,
  marketRefreshSchedule,
} from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

function renderLearning(wsId = WS_ID) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workspaces/${wsId}/learn`]}>
        <Routes>
          <Route path="/workspaces/:workspaceId/learn" element={<Learning />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Learning', () => {
  describe('learning summary', () => {
    it('shows KPI tiles for videos observed, recommendations, opportunities, experiments, and creative-factor patterns', async () => {
      renderLearning()
      await waitFor(() => screen.getByText('Videos observed'))
      expect(screen.getByText('Recommendations', { selector: '.metric-card-label' })).toBeInTheDocument()
      expect(screen.getByText('Market opportunities')).toBeInTheDocument()
      expect(screen.getByText('Active experiments')).toBeInTheDocument()
      expect(screen.getByText('Creative-factor patterns')).toBeInTheDocument()
    })
  })

  describe('empty states (no data seeded)', () => {
    it('shows an honest "not enough videos" state for channel evidence', async () => {
      renderLearning()
      await waitFor(() => screen.getByText(/not enough videos yet/i))
    })

    it('shows an explicit "no recommendations" unavailable state', async () => {
      renderLearning()
      await waitFor(() => screen.getByText(/no recommendations yet/i))
    })

    it('does not show fake recommendation data', () => {
      renderLearning()
      expect(screen.queryByText(/confidence: 0\.\d/)).not.toBeInTheDocument()
    })

    it('shows an honest empty state for creative factors, naming the unlock command', async () => {
      renderLearning()
      await waitFor(() => screen.getByText(/cross-publication learning hasn't run yet/i))
      expect(screen.getByText(/ace learn cross-pub --channel/i)).toBeInTheDocument()
    })

    it('shows an honest empty state for market opportunities', async () => {
      renderLearning()
      await waitFor(() => screen.getByText(/no market opportunities tracked yet/i))
    })

    it('shows an honest empty state for recommended experiments', async () => {
      renderLearning()
      await waitFor(() => screen.getByText(/no experiment proposals yet/i))
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

    it('keeps cross-publication maturity and recommendation confidence as separate documented scales', async () => {
      renderLearning()
      await waitFor(() => screen.getByText('Cross-publication maturity'))
      const tile = screen.getByText('Cross-publication maturity').closest('div')!
      expect(tile.textContent).toContain('separate scale')
    })

    it('page body does not make bare causal claims ("X causes Y")', async () => {
      renderLearning()
      await waitFor(() => screen.getByText('Learn'))
      const body = document.body.textContent ?? ''
      expect(body).not.toMatch(/\bcauses\s+(?!not\b)\w+/i)
    })

    it('labels market opportunities as external evidence, distinct from Orvella performance', async () => {
      renderLearning()
      await waitFor(() => screen.getByText(/external market evidence/i))
    })
  })

  describe('populated state — recommendations', () => {
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
      expect(screen.queryByText(/no recommendations yet/i)).not.toBeInTheDocument()
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
      await waitFor(() =>
        screen.getByTestId(`recommendation-${recommendationAccepted.id}`),
      )
      const card = screen.getByTestId(`recommendation-${recommendationAccepted.id}`)
      expect(within(card).getByText('accepted')).toBeInTheDocument()
    })

    it('filters recommendations by status', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/recommendations`, () =>
          HttpResponse.json([recommendation, recommendationAccepted]),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText(recommendation.title))
      fireEvent.click(screen.getByTestId('filter-accepted'))
      await waitFor(() => {
        expect(screen.queryByText(recommendation.title)).not.toBeInTheDocument()
        expect(screen.getByText(recommendationAccepted.title)).toBeInTheDocument()
      })
    })

    it('still shows confidence model section when data is loaded', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/recommendations`, () =>
          HttpResponse.json([recommendation]),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText(recommendation.title))
      expect(screen.getByText('Confidence & evidence model')).toBeInTheDocument()
    })
  })

  describe('populated state — channel evidence', () => {
    it('compares videos by avg % viewed when at least two have analytics', async () => {
      const secondPub = { ...publicationListItem, id: 3, title: 'Second Video' }
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/publications`, () =>
          HttpResponse.json([publicationListItem, secondPub]),
        ),
        http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/analytics`, () =>
          HttpResponse.json({ ...publicationAnalytics, metrics: { average_view_percentage: 95.6 } }),
        ),
        http.get(`${B}/workspaces/${WS_ID}/publications/3/analytics`, () =>
          HttpResponse.json({ ...publicationAnalytics, snapshot_id: 2, metrics: { average_view_percentage: 36.3 } }),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText(publicationListItem.title))
      expect(screen.getByText('Second Video')).toBeInTheDocument()
      expect(screen.queryByText(/not enough videos yet/i)).not.toBeInTheDocument()
    })
  })

  describe('populated state — market intelligence', () => {
    it('shows an opportunity card with its topic and attractiveness score', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/market/opportunities`, () =>
          HttpResponse.json([marketOpportunity]),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText(marketOpportunity.canonical_label!))
      expect(screen.getByText('62%')).toBeInTheDocument()
    })

    it('shows low competition as a plain-language label, not a raw inverted score', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/market/opportunities`, () =>
          HttpResponse.json([{ ...marketOpportunity, score_competition: 0.9 }]),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText(/low competition/i))
    })

    it('shows an absent sub-score as "not available", never as zero', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/market/opportunities`, () =>
          HttpResponse.json([marketOpportunity]),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText(marketOpportunity.canonical_label!))
      // score_trend_strength is null/absent in the fixture.
      expect(screen.getByText(/not available yet/i)).toBeInTheDocument()
    })

    it('shows last/next market refresh time when a recurring schedule exists', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/schedules`, () =>
          HttpResponse.json([marketRefreshSchedule]),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText(/last refreshed/i))
      expect(screen.getByText(/next refresh/i)).toBeInTheDocument()
    })

    it('does not show a refresh line when no recurring schedule exists', async () => {
      renderLearning()
      await waitFor(() => screen.getByText('YouTube trends'))
      expect(screen.queryByText(/last refreshed/i)).not.toBeInTheDocument()
    })

    describe('evidence drill-down', () => {
      it('is collapsed by default', async () => {
        server.use(
          http.get(`${B}/workspaces/${WS_ID}/market/opportunities`, () =>
            HttpResponse.json([marketOpportunity]),
          ),
        )
        renderLearning()
        await waitFor(() => screen.getByText(marketOpportunity.canonical_label!))
        expect(screen.queryByText(/external market evidence — why/i)).not.toBeInTheDocument()
      })

      it('expands to show labeled evidence grouped by observation, not raw JSON', async () => {
        server.use(
          http.get(`${B}/workspaces/${WS_ID}/market/opportunities`, () =>
            HttpResponse.json([marketOpportunity]),
          ),
          http.get(`${B}/workspaces/${WS_ID}/market/opportunities/${marketOpportunity.id}/evidence`, () =>
            HttpResponse.json(opportunityEvidenceResponse),
          ),
        )
        renderLearning()
        await waitFor(() => screen.getByText(/22 evidence signals/i))
        fireEvent.click(screen.getByText(/22 evidence signals/i))
        await waitFor(() => screen.getByText(/external market evidence — why/i))
        await waitFor(() => screen.getByText('Audience demand'))
        expect(screen.getByText('actionable')).toBeInTheDocument()
        expect(screen.queryByText(/"evidence_type"/)).not.toBeInTheDocument()
      })
    })
  })

  describe('populated state — recommended experiments', () => {
    it('shows a strategy brief with its real reasoning fields', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/market/strategy-briefs`, () =>
          HttpResponse.json([experimentStrategyBrief]),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText(experimentStrategyBrief.hypothesis))
      expect(screen.getByText(new RegExp(experimentStrategyBrief.strategic_reason))).toBeInTheDocument()
    })
  })

  describe('populated state — creative factors', () => {
    it('groups a feature observation under its category with a maturity badge', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/cross-publication`, () =>
          HttpResponse.json({
            channel_id: CH_ID,
            baselines: [channelPerformanceBaseline],
            feature_observations: [featurePerformanceObservation],
          }),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText('Scene count'))
      expect(screen.getByText('Production')).toBeInTheDocument()
      const row = screen.getByText('Scene count').closest('.factor-observation-row') as HTMLElement
      expect(within(row).getByText('Exploratory')).toBeInTheDocument()
    })
  })
})
