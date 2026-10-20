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
import { creativeFactorLabel } from '@/lib/marketIntelligence'
import { Learning } from './Learning'
import {
  WS_ID,
  CH_ID,
  PUB_ID,
  recommendation,
  recommendationAccepted,
  recommendationRejected,
  recommendationSupersededV1,
  recommendationSupersededV2,
  recommendationCurrentV3,
  recommendationAcceptedStale,
  marketOpportunity,
  experimentStrategyBrief,
  channelPerformanceBaseline,
  featurePerformanceObservation,
  topicAttributePerformanceObservation,
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

    it('shows an honest empty state for topic attribute insights, naming the unlock commands', async () => {
      renderLearning()
      await waitFor(() => screen.getByText(/no topic-attribute evidence yet/i))
      expect(screen.getByText(/ace learn topic-attributes backfill --channel/i)).toBeInTheDocument()
      expect(screen.getByText(/ace learn topic-attributes recompute --channel/i)).toBeInTheDocument()
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

  describe('Phase 18F — default "current" view excludes stale/superseded/rejected history', () => {
    function seedFullLineage() {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/recommendations`, () =>
          HttpResponse.json([
            recommendationSupersededV1,
            recommendationSupersededV2,
            recommendationCurrentV3,
            recommendationRejected,
            recommendationAccepted,
            recommendationAcceptedStale,
          ]),
        ),
      )
    }

    it('defaults to the "current" tab', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/recommendations`, () =>
          HttpResponse.json([recommendation]),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByTestId('filter-current'))
      expect(screen.getByTestId('filter-current')).toHaveClass('button-primary')
    })

    it('current view shows only the pending v3 and the valid accepted row', async () => {
      seedFullLineage()
      renderLearning()
      await waitFor(() => screen.getByText(recommendationCurrentV3.title))
      expect(screen.getByText(recommendationAccepted.title)).toBeInTheDocument()
      expect(screen.queryByText(recommendationRejected.title)).not.toBeInTheDocument()
      expect(screen.queryByTestId(`recommendation-${recommendationSupersededV1.id}`)).not.toBeInTheDocument()
      expect(screen.queryByTestId(`recommendation-${recommendationSupersededV2.id}`)).not.toBeInTheDocument()
      expect(screen.queryByTestId(`recommendation-${recommendationAcceptedStale.id}`)).not.toBeInTheDocument()
    })

    it('accepted tab shows every accepted row, including stale evidence, with a stale badge', async () => {
      seedFullLineage()
      renderLearning()
      await waitFor(() => screen.getByText(recommendationCurrentV3.title))
      fireEvent.click(screen.getByTestId('filter-accepted'))
      await waitFor(() => screen.getByTestId(`recommendation-${recommendationAcceptedStale.id}`))
      expect(screen.getByText(recommendationAccepted.title)).toBeInTheDocument()
      const staleCard = screen.getByTestId(`recommendation-${recommendationAcceptedStale.id}`)
      expect(within(staleCard).getByText('stale evidence')).toBeInTheDocument()
      // the valid accepted row must NOT carry the stale badge
      const validCard = screen.getByTestId(`recommendation-${recommendationAccepted.id}`)
      expect(within(validCard).queryByText('stale evidence')).not.toBeInTheDocument()
    })

    it('rejected tab shows only rejected rows', async () => {
      seedFullLineage()
      renderLearning()
      await waitFor(() => screen.getByText(recommendationCurrentV3.title))
      fireEvent.click(screen.getByTestId('filter-rejected'))
      await waitFor(() => screen.getByText(recommendationRejected.title))
      expect(screen.queryByText(recommendationCurrentV3.title)).not.toBeInTheDocument()
    })

    it('history tab shows the full lineage, including superseded and stale-accepted rows', async () => {
      seedFullLineage()
      renderLearning()
      await waitFor(() => screen.getByText(recommendationCurrentV3.title))
      fireEvent.click(screen.getByTestId('filter-history'))
      await waitFor(() => screen.getByTestId(`recommendation-${recommendationSupersededV1.id}`))
      expect(screen.getByTestId(`recommendation-${recommendationSupersededV2.id}`)).toBeInTheDocument()
      expect(screen.getByTestId(`recommendation-${recommendationCurrentV3.id}`)).toBeInTheDocument()
      expect(screen.getByTestId(`recommendation-${recommendationRejected.id}`)).toBeInTheDocument()
      expect(screen.getByTestId(`recommendation-${recommendationAcceptedStale.id}`)).toBeInTheDocument()
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
    it('groups a feature observation under its category with a maturity badge (via show-all-raw, since a lone single-bucket observation has no variation to show by default)', async () => {
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
      await waitFor(() => screen.getByText(/not enough variation yet/i))
      fireEvent.click(screen.getByTestId('toggle-show-all-raw-factors'))
      await waitFor(() => screen.getByText('Scene count'))
      expect(screen.getByText('Production')).toBeInTheDocument()
      const row = screen.getByText('Scene count').closest('.factor-observation-row') as HTMLElement
      expect(within(row).getByText('Exploratory')).toBeInTheDocument()
    })
  })

  describe('Phase 18H.C — Topic Attribute Insights', () => {
    it('renders a decision-useful observation by default, with maturity, publication, and domain counts', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/topic-attributes`, () =>
          HttpResponse.json({
            channel_id: CH_ID,
            raw: [topicAttributePerformanceObservation],
            decision_useful: [topicAttributePerformanceObservation],
          }),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText('Practical utility'))
      const row = screen.getByText('Practical utility').closest('.factor-observation-row') as HTMLElement
      expect(within(row).getByText('Exploratory')).toBeInTheDocument()
      expect(within(row).getByText(/2 publications/)).toBeInTheDocument()
      expect(within(row).getByText(/2 domains/)).toBeInTheDocument()
    })

    it('keeps Topic Attribute Insights visually and structurally separate from Creative Factors', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/cross-publication`, () =>
          HttpResponse.json({
            channel_id: CH_ID,
            baselines: [channelPerformanceBaseline],
            feature_observations: [
              { ...featurePerformanceObservation, sample_maturity: 'directional', publication_count: 5 },
              {
                ...featurePerformanceObservation,
                id: 999,
                feature_bucket: '10-13',
                sample_maturity: 'directional',
                publication_count: 4,
              },
            ],
          }),
        ),
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/topic-attributes`, () =>
          HttpResponse.json({
            channel_id: CH_ID,
            raw: [topicAttributePerformanceObservation],
            decision_useful: [topicAttributePerformanceObservation],
          }),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText('Practical utility'))
      expect(screen.getByText('Topic Attribute Insights')).toBeInTheDocument()
      expect(screen.getByText('Creative factors')).toBeInTheDocument()
      // The creative-factor row (Scene count) and the attribute row
      // (Practical utility) must not be rendered inside the same card.
      const attributeRow = screen.getByText('Practical utility').closest('.factor-observation-row') as HTMLElement
      expect(within(attributeRow).queryByText('Scene count')).not.toBeInTheDocument()
    })

    it('never uses causal language for a topic-attribute observation', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/topic-attributes`, () =>
          HttpResponse.json({
            channel_id: CH_ID,
            raw: [topicAttributePerformanceObservation],
            decision_useful: [topicAttributePerformanceObservation],
          }),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText('Practical utility'))
      const section = screen.getByText('Topic Attribute Insights').closest('.section') as HTMLElement
      expect(within(section).queryByText(/\bcauses\b/i)).not.toBeInTheDocument()
      expect(within(section).queryByText(/\bguarantees\b/i)).not.toBeInTheDocument()
      expect(within(section).queryByText(/\balways performs\b/i)).not.toBeInTheDocument()
    })

    it('"Show all raw observations" reveals insufficient/single-domain rows hidden by default', async () => {
      const insufficientOb = {
        ...topicAttributePerformanceObservation,
        id: 202,
        attribute_name: 'curiosity',
        sample_maturity: 'insufficient',
        publication_count: 1,
        distinct_domain_family_count: 1,
      }
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/topic-attributes`, () =>
          HttpResponse.json({
            channel_id: CH_ID,
            raw: [topicAttributePerformanceObservation, insufficientOb],
            decision_useful: [topicAttributePerformanceObservation],
          }),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText('Practical utility'))
      expect(screen.queryByText('Curiosity')).not.toBeInTheDocument()
      fireEvent.click(screen.getByTestId('toggle-show-all-raw-attributes'))
      await waitFor(() => screen.getByText('Curiosity'))
      expect(screen.getByText('Practical utility')).toBeInTheDocument()
    })
  })

  describe('Phase 18F.1 — creative-factor pattern display filtering', () => {
    const usefulOb = {
      ...featurePerformanceObservation,
      id: 101,
      feature_name: 'scene_count',
      feature_bucket: '5-8',
      metric_name: 'views',
      sample_maturity: 'directional',
      publication_count: 5,
    }
    const usefulObOtherBucket = {
      ...featurePerformanceObservation,
      id: 102,
      feature_name: 'scene_count',
      feature_bucket: '9-12',
      metric_name: 'views',
      sample_maturity: 'directional',
      publication_count: 4,
    }
    const invariantOb = {
      ...featurePerformanceObservation,
      id: 103,
      feature_name: 'has_hook',
      feature_bucket: 'true',
      metric_name: 'views',
      sample_maturity: 'actionable',
      publication_count: 10,
    }
    const insufficientOb = {
      ...featurePerformanceObservation,
      id: 104,
      feature_name: 'script_format',
      feature_bucket: 'short',
      metric_name: 'views',
      sample_maturity: 'insufficient',
      publication_count: 1,
    }
    const insufficientObOtherBucket = {
      ...featurePerformanceObservation,
      id: 105,
      feature_name: 'script_format',
      feature_bucket: 'long_form',
      metric_name: 'views',
      sample_maturity: 'insufficient',
      publication_count: 1,
    }

    function seedFactors() {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/cross-publication`, () =>
          HttpResponse.json({
            channel_id: CH_ID,
            baselines: [channelPerformanceBaseline],
            feature_observations: [
              usefulOb,
              usefulObOtherBucket,
              invariantOb,
              insufficientOb,
              insufficientObOtherBucket,
            ],
          }),
        ),
      )
    }

    it('hides a single-value / invariant pattern by default', async () => {
      seedFactors()
      renderLearning()
      await waitFor(() => screen.getAllByText(creativeFactorLabel('scene_count'))[0])
      expect(screen.queryByText(creativeFactorLabel('has_hook'))).not.toBeInTheDocument()
    })

    it('hides an n=1 / insufficient-support pattern by default', async () => {
      seedFactors()
      renderLearning()
      await waitFor(() => screen.getAllByText(creativeFactorLabel('scene_count'))[0])
      expect(screen.queryByText(creativeFactorLabel('script_format'))).not.toBeInTheDocument()
    })

    it('shows a supported comparative pattern by default', async () => {
      seedFactors()
      renderLearning()
      await waitFor(() => screen.getAllByText(creativeFactorLabel('scene_count'))[0])
      expect(screen.getAllByText(creativeFactorLabel('scene_count')).length).toBe(2)
    })

    it('"Show all raw patterns" reveals the hidden raw rows', async () => {
      seedFactors()
      renderLearning()
      await waitFor(() => screen.getAllByText(creativeFactorLabel('scene_count'))[0])
      expect(screen.queryByText(creativeFactorLabel('has_hook'))).not.toBeInTheDocument()
      fireEvent.click(screen.getByTestId('toggle-show-all-raw-factors'))
      await waitFor(() => screen.getByText(creativeFactorLabel('has_hook')))
      expect(screen.getAllByText(creativeFactorLabel('script_format'))[0]).toBeInTheDocument()
    })

    it('raw total count stays accurate regardless of the toggle', async () => {
      seedFactors()
      renderLearning()
      await waitFor(() => screen.getByText(/5 raw observations/))
      fireEvent.click(screen.getByTestId('toggle-show-all-raw-factors'))
      await waitFor(() => screen.getByText(/showing all 5 raw observations/i))
    })

    it('the useful/displayed count is accurate', async () => {
      seedFactors()
      renderLearning()
      await waitFor(() => screen.getByText(/2 useful patterns/))
    })

    it('raw rows are never deleted from the API response — toggling only changes what is shown', async () => {
      seedFactors()
      renderLearning()
      await waitFor(() => screen.getAllByText(creativeFactorLabel('scene_count'))[0])
      fireEvent.click(screen.getByTestId('toggle-show-all-raw-factors'))
      await waitFor(() => screen.getByText(creativeFactorLabel('has_hook')))
      fireEvent.click(screen.getByTestId('toggle-show-all-raw-factors'))
      await waitFor(() => screen.getByText(/2 useful patterns/))
      // toggling back off does not lose scene_count — the underlying raw
      // data was never mutated, only re-filtered for display.
      expect(screen.getAllByText(creativeFactorLabel('scene_count')).length).toBe(2)
    })

    it('a legacy row with missing sample_maturity fails safe (hidden by default, still shown via raw toggle)', async () => {
      const legacyOb = {
        ...featurePerformanceObservation,
        id: 106,
        feature_name: 'render_caption_burn_in',
        feature_bucket: 'true',
        metric_name: 'views',
        sample_maturity: '',
        publication_count: 3,
      }
      const legacyObOtherBucket = {
        ...featurePerformanceObservation,
        id: 107,
        feature_name: 'render_caption_burn_in',
        feature_bucket: 'false',
        metric_name: 'views',
        sample_maturity: '',
        publication_count: 3,
      }
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/cross-publication`, () =>
          HttpResponse.json({
            channel_id: CH_ID,
            baselines: [channelPerformanceBaseline],
            feature_observations: [legacyOb, legacyObOtherBucket],
          }),
        ),
      )
      renderLearning()
      await waitFor(() => screen.getByText(/not enough variation yet/i))
      expect(screen.queryByText(creativeFactorLabel('render_caption_burn_in'))).not.toBeInTheDocument()
      fireEvent.click(screen.getByTestId('toggle-show-all-raw-factors'))
      await waitFor(() => screen.getAllByText(creativeFactorLabel('render_caption_burn_in'))[0])
    })
  })
})
