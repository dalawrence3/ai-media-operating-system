/* Dashboard page — Phase 17B command-center rewrite.
   Covers: loading, populated (KPIs/videos/pipeline), sparse analytics,
   API error, attention-area rendering, and recommendation maturity display. */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { server } from '@/test/server'
import { Dashboard } from './Dashboard'
import {
  WS_ID,
  CH_ID,
  healthView,
  cpChannel2,
  cpAccount1,
  publicationListItem,
  recommendation,
  channelAutomationPolicyResponse,
  publishingAuthorizationAuthorized,
  publishingAuthorizationUnauthorized,
} from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

function renderDashboard(wsId = WS_ID) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workspaces/${wsId}/dashboard`]}>
        <Routes>
          <Route path="/workspaces/:workspaceId/dashboard" element={<Dashboard />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Dashboard', () => {
  describe('loading state', () => {
    it('shows a loading indicator before publications resolve', () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/publications`, async () => {
          await new Promise(r => setTimeout(r, 300))
          return HttpResponse.json([publicationListItem])
        }),
      )
      renderDashboard()
      expect(screen.getByText(/loading dashboard/i)).toBeInTheDocument()
    })
  })

  describe('populated state', () => {
    it('shows the channel identity in the header, not the workspace ID', async () => {
      renderDashboard()
      await waitFor(() =>
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(cpAccount1.display_name),
      )
      expect(screen.queryByText(WS_ID)).not.toBeInTheDocument()
    })

    it('renders real KPI values formatted, not raw numbers', async () => {
      renderDashboard()
      await waitFor(() => screen.getAllByText('Views').length > 0)
      // "Views" also labels the chart's metric toggle — scope to the KPI label specifically.
      const viewsLabel = screen.getByText('Views', { selector: '.metric-card-label' })
      const viewsCard = viewsLabel.closest('.metric-card') as HTMLElement
      // publicationAnalytics.metrics.views === 1234 — compact-formatted, not the raw digits.
      expect(within(viewsCard).getByText('1.2K')).toBeInTheDocument()
      expect(within(viewsCard).queryByText('1234')).not.toBeInTheDocument()
      expect(within(viewsCard).getByText(/across 1 of 1 video/)).toBeInTheDocument()
    })

    it('shows the video in Recent videos with its title', async () => {
      renderDashboard()
      await waitFor(() => screen.getByText('Recent videos'))
      expect(screen.getByText(publicationListItem.title)).toBeInTheDocument()
    })

    it('counts the video in the content pipeline as On YouTube', async () => {
      renderDashboard()
      await waitFor(() => screen.getByText('Content pipeline'))
      const publishedLabel = screen.getByText('On YouTube', { selector: '.metric-card-label' })
      const publishedCard = publishedLabel.closest('.metric-card') as HTMLElement
      expect(within(publishedCard).getByText('1')).toBeInTheDocument()
    })

    it('never renders a raw publication ID on the video card', async () => {
      renderDashboard()
      await waitFor(() => screen.getByText('Recent videos'))
      const videoCard = screen.getByText(publicationListItem.title).closest('.video-card') as HTMLElement
      // The card must identify the video by title/thumbnail/metrics only —
      // never by printing its bare database ID (e.g. "1") as a label.
      expect(within(videoCard).queryByText(/^#?\s*1$/)).not.toBeInTheDocument()
    })
  })

  describe('sparse / no analytics', () => {
    it('shows an honest empty state for the performance chart when no video has data', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/publications/${publicationListItem.id}/analytics`, () =>
          HttpResponse.json({
            snapshot_id: null,
            snapshot_ingested_at: null,
            period_start: null,
            period_end: null,
            metrics: {},
            retention_point_count: 0,
          }),
        ),
      )
      renderDashboard()
      await waitFor(() => screen.getByText('Channel performance'))
      // Scope to the empty-state title specifically — the KPI sub-label also
      // says "No analytics data yet" for the same reason.
      expect(
        screen.getByText('No analytics data yet', { selector: '.empty-state-title' }),
      ).toBeInTheDocument()
    })

    it('excludes videos with no analytics from KPI sums rather than showing zero', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/publications/${publicationListItem.id}/analytics`, () =>
          HttpResponse.json({
            snapshot_id: null, snapshot_ingested_at: null,
            period_start: null, period_end: null, metrics: {}, retention_point_count: 0,
          }),
        ),
      )
      renderDashboard()
      await waitFor(() => screen.getAllByText('Views').length > 0)
      const viewsLabel = screen.getByText('Views', { selector: '.metric-card-label' })
      const viewsCard = viewsLabel.closest('.metric-card') as HTMLElement
      expect(within(viewsCard).getByText('—')).toBeInTheDocument()
      expect(within(viewsCard).getByText(/no analytics data yet/i)).toBeInTheDocument()
    })

    it('shows an honest empty state for Learning snapshot with no recommendations', async () => {
      renderDashboard()
      await waitFor(() => screen.getByText('What the system is learning'))
      expect(screen.getByText(/not enough data yet/i)).toBeInTheDocument()
    })
  })

  describe('API error', () => {
    it('shows an error state when publications fail to load', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/publications`, () =>
          HttpResponse.json({ detail: 'Internal error' }, { status: 500 }),
        ),
      )
      renderDashboard()
      await waitFor(() => screen.getByRole('alert'))
    })
  })

  describe('attention area', () => {
    it('renders attention items when there are pending reviews (default fixtures)', async () => {
      renderDashboard()
      await waitFor(() => screen.getByText(/pending review/i))
      expect(screen.getByText(/pending review/i)).toBeInTheDocument()
    })

    it('does not render attention items when nothing needs attention', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/review-queue`, () => HttpResponse.json([])),
        http.get(`${B}/workspaces/${WS_ID}/exceptions`, () => HttpResponse.json([])),
        http.get(`${B}/workspaces/${WS_ID}/health`, () => HttpResponse.json(healthView)),
      )
      renderDashboard()
      await waitFor(() => screen.getByText('Content pipeline'))
      expect(screen.queryByText(/pending review/i)).not.toBeInTheDocument()
      expect(document.querySelector('.attention-strip')).not.toBeInTheDocument()
    })

    it('flags a degraded YouTube connection with error severity', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/review-queue`, () => HttpResponse.json([])),
        http.get(`${B}/workspaces/${WS_ID}/exceptions`, () => HttpResponse.json([])),
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts`, () =>
          HttpResponse.json([{ ...cpAccount1, status: 'credential_invalid' }]),
        ),
      )
      renderDashboard()
      await waitFor(() => screen.getByText(/youtube connection/i))
      const item = screen.getByText(/youtube connection/i).closest('.attention-item')
      expect(item).toHaveClass('attention-error')
    })

    it('shows exceptions with error severity, reviews with info severity', async () => {
      renderDashboard()
      await waitFor(() => screen.getByText(/pending review/i))
      const reviewItem = screen.getByText(/pending review/i).closest('.attention-item')
      expect(reviewItem).toHaveClass('attention-info')
      const exceptionItem = screen.getByText(/exception/i).closest('.attention-item')
      expect(exceptionItem).toHaveClass('attention-error')
    })
  })

  describe('recommendation maturity', () => {
    it('shows a maturity badge and filters out [DEV] fixtures', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/recommendations`, () =>
          HttpResponse.json([
            recommendation,
            { ...recommendation, id: 99, title: '[DEV] Fixture recommendation', publication_id: null },
          ]),
        ),
      )
      renderDashboard()
      await waitFor(() => screen.getByText(recommendation.title))
      expect(screen.queryByText(/\[DEV\]/)).not.toBeInTheDocument()
      // recommendation.recommendation_strength === 'actionable'
      expect(screen.getByText('Actionable')).toBeInTheDocument()
    })
  })

  describe('publishing status strip', () => {
    it('shows a healthy "Rate limit reached" state, not "Blocked", when the only blocker is the 24h ceiling', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/automation-policy`, () =>
          HttpResponse.json(channelAutomationPolicyResponse),
        ),
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/publishing-authorization`, () =>
          HttpResponse.json({
            authorization: publishingAuthorizationAuthorized.authorization,
            decision: {
              ...publishingAuthorizationAuthorized.decision,
              allowed: false,
              blocked_by: ['rate_limit_reached'],
              detail: 'Blocked by: rate_limit_reached',
              publications_last_24h: 1,
              max_publications_per_24h: 1,
            },
          }),
        ),
      )
      renderDashboard()
      await waitFor(() => screen.getByText('Autonomous publishing'))
      expect(screen.getByText('Rate limit reached')).toBeInTheDocument()
      expect(screen.queryByText('Blocked')).not.toBeInTheDocument()
      expect(screen.getByText(/1\/1 publications in the last 24h/)).toBeInTheDocument()
      expect(screen.getByText(/resumes automatically once the rolling window clears/)).toBeInTheDocument()
    })

    it('still shows "Blocked" for a genuine, actionable blocker', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/automation-policy`, () =>
          HttpResponse.json(channelAutomationPolicyResponse),
        ),
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/publishing-authorization`, () =>
          HttpResponse.json({
            authorization: publishingAuthorizationAuthorized.authorization,
            decision: {
              ...publishingAuthorizationAuthorized.decision,
              allowed: false,
              blocked_by: ['account_unhealthy'],
              detail: 'Blocked by: account_unhealthy',
              account_status: 'credential_invalid',
            },
          }),
        ),
      )
      renderDashboard()
      await waitFor(() => screen.getByText('Autonomous publishing'))
      expect(screen.getByText('Blocked')).toBeInTheDocument()
      expect(screen.queryByText('Rate limit reached')).not.toBeInTheDocument()
      expect(screen.queryByText(/resumes automatically/)).not.toBeInTheDocument()
    })

    it('still shows "Blocked" when the channel is not authorized, even alongside a rate-limit reason', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/automation-policy`, () =>
          HttpResponse.json(channelAutomationPolicyResponse),
        ),
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/publishing-authorization`, () =>
          HttpResponse.json(publishingAuthorizationUnauthorized),
        ),
      )
      renderDashboard()
      await waitFor(() => screen.getByText('Autonomous publishing'))
      expect(screen.getByText('Not authorized')).toBeInTheDocument()
      expect(screen.queryByText('Rate limit reached')).not.toBeInTheDocument()
    })

    it('shows "Ready to publish" and no rate-limit note in the normal allowed state', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/automation-policy`, () =>
          HttpResponse.json(channelAutomationPolicyResponse),
        ),
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/publishing-authorization`, () =>
          HttpResponse.json(publishingAuthorizationAuthorized),
        ),
      )
      renderDashboard()
      await waitFor(() => screen.getByText('Autonomous publishing'))
      expect(screen.getByText('Ready to publish')).toBeInTheDocument()
      expect(screen.queryByText('Rate limit reached')).not.toBeInTheDocument()
      expect(screen.queryByText('Blocked')).not.toBeInTheDocument()
      expect(screen.queryByText(/resumes automatically/)).not.toBeInTheDocument()
    })
  })

  describe('multi-channel safety', () => {
    it('resolves the first channel without hardcoding an ID or name', async () => {
      // cpChannel and cpChannel2 are both present in the default handler list;
      // the dashboard must consistently show channels[0]'s identity.
      renderDashboard()
      await waitFor(() =>
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(cpAccount1.display_name),
      )
      expect(screen.queryByText(cpChannel2.name)).not.toBeInTheDocument()
    })
  })
})
