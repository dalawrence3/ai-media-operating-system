/* Channels page — Phase 17B.1 productized single-channel view.
   Covers: channel identity, YouTube account card, analytics permission,
   strategy, create channel, add account modals. */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { server } from '@/test/server'
import { Channels } from './Channels'
import {
  WS_ID, CH_ID, cpChannel, cpAccount1, channelStrategyResponse, autonomyReadinessResponse,
  channelAutomationPolicyResponse, publishingAuthorizationAuthorized,
} from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

function renderChannels(wsId = WS_ID) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return {
    user: userEvent.setup(),
    ...render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/workspaces/${wsId}/channels`]}>
          <Routes>
            <Route path="/workspaces/:workspaceId/channels" element={<Channels />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  }
}

describe('Channels', () => {
  describe('channel identity', () => {
    it('shows the primary account display name as the page title', async () => {
      renderChannels()
      await waitFor(() =>
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(cpAccount1.display_name),
      )
    })

    it('shows channel identifiers in technical details', async () => {
      const user = userEvent.setup()
      renderChannels()
      await waitFor(() => screen.getByText('Channel identifiers'))
      await user.click(screen.getByText('Channel identifiers'))
      expect(screen.getByText(cpChannel.name)).toBeInTheDocument()
      expect(screen.getByText(`/${cpChannel.slug}`)).toBeInTheDocument()
    })
  })

  describe('YouTube account card', () => {
    it('renders account display names', async () => {
      renderChannels()
      await waitFor(() => screen.getByRole('heading', { level: 1 }))
      const cards = document.querySelectorAll('.card')
      expect(cards.length).toBeGreaterThan(0)
    })

    it('shows connect button for disconnected accounts', async () => {
      renderChannels()
      await waitFor(() => screen.getByRole('heading', { level: 1 }))
      await waitFor(() => {
        const connectBtns = screen.getAllByRole('button', { name: /connect to youtube/i })
        expect(connectBtns.length).toBeGreaterThanOrEqual(1)
      })
    })
  })

  describe('analytics permission', () => {
    it('shows enable analytics button when scope is not granted', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts/:accountId/connection`, () =>
          HttpResponse.json({
            account_id: 'mock',
            connected: true,
            provider_channel_id: 'UCtest',
            channel_title: 'Test Channel',
            verified_at: null,
            granted_scopes: ['youtube.readonly'],
            upload_scope_granted: true,
            analytics_scope_granted: false,
            credential_status: 'valid',
            health_status: 'healthy',
          }),
        ),
      )
      renderChannels()
      await waitFor(() => screen.getAllByRole('button', { name: /enable analytics permission/i }))
      expect(screen.getAllByRole('button', { name: /enable analytics permission/i }).length).toBeGreaterThanOrEqual(1)
    })

    it('hides enable button when analytics scope is granted', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts/:accountId/connection`, () =>
          HttpResponse.json({
            account_id: 'mock',
            connected: true,
            provider_channel_id: 'UCtest',
            channel_title: 'Test Channel',
            verified_at: null,
            granted_scopes: ['youtube.readonly', 'yt-analytics.readonly'],
            upload_scope_granted: true,
            analytics_scope_granted: true,
            credential_status: 'valid',
            health_status: 'healthy',
          }),
        ),
      )
      renderChannels()
      await waitFor(() => screen.getByRole('heading', { level: 1 }))
      await waitFor(() => screen.getByText('Verify connection'))
      expect(screen.queryByRole('button', { name: /enable analytics permission/i })).not.toBeInTheDocument()
    })
  })

  describe('strategy', () => {
    it('shows message when no strategy is assigned', async () => {
      renderChannels()
      await waitFor(() => screen.getByText(/no strategy profile assigned/i))
    })

    it('offers a "Set up strategy" action when none is assigned', async () => {
      renderChannels()
      await waitFor(() => screen.getByRole('button', { name: /set up strategy/i }))
    })

    describe('with an active profile', () => {
      function useProfile() {
        server.use(
          http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/strategy`, () =>
            HttpResponse.json(channelStrategyResponse),
          ),
        )
      }

      it('shows the effective mode, evidence maturity, and current weighting', async () => {
        useProfile()
        renderChannels()
        await waitFor(() => screen.getByText('Bootstrap exploration'))
        expect(screen.getByText('Not enough evidence')).toBeInTheDocument()
        expect(screen.getByText(/80% market intelligence \/ 20% channel evidence/)).toBeInTheDocument()
      })

      it('shows exploration progress against the bootstrap target', async () => {
        useProfile()
        renderChannels()
        await waitFor(() => screen.getByText('0 / 18'))
      })

      it('shows the diversity rule in plain language', async () => {
        useProfile()
        renderChannels()
        await waitFor(() => screen.getByText(/max 40% of the portfolio from one cluster, at most 2 in a row/i))
      })

      it('shows creative dimensions as tags, never a topic list', async () => {
        useProfile()
        renderChannels()
        await waitFor(() => screen.getByText('Topic / theme'))
        expect(screen.getByText('Hook')).toBeInTheDocument()
        expect(screen.getByText('Pacing')).toBeInTheDocument()
      })

      it('keeps the raw config behind a technical-details disclosure', async () => {
        useProfile()
        renderChannels()
        await waitFor(() => screen.getByText(/strategy configuration/i))
        const details = screen.getByText(/strategy configuration/i).closest('details')!
        expect(details).not.toHaveAttribute('open')
      })

      it('offers an "Edit strategy" action', async () => {
        useProfile()
        renderChannels()
        await waitFor(() => screen.getByRole('button', { name: /edit strategy/i }))
      })
    })

    describe('editing', () => {
      it('opens the edit modal with current values pre-filled', async () => {
        server.use(
          http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/strategy`, () =>
            HttpResponse.json(channelStrategyResponse),
          ),
        )
        const { user } = renderChannels()
        await waitFor(() => screen.getByRole('button', { name: /edit strategy/i }))
        await user.click(screen.getByRole('button', { name: /edit strategy/i }))
        expect(screen.getByRole('dialog')).toBeInTheDocument()
        expect(screen.getByLabelText(/exploration publication target/i)).toHaveValue(18)
      })

      it('saves a new version and closes the modal', async () => {
        server.use(
          http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/strategy`, () =>
            HttpResponse.json(channelStrategyResponse),
          ),
        )
        const { user } = renderChannels()
        await waitFor(() => screen.getByRole('button', { name: /edit strategy/i }))
        await user.click(screen.getByRole('button', { name: /edit strategy/i }))
        await user.click(screen.getByRole('button', { name: /save as new version/i }))
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      })

      it('disables save when the publication target is invalid', async () => {
        const { user } = renderChannels()
        await waitFor(() => screen.getByRole('button', { name: /set up strategy/i }))
        await user.click(screen.getByRole('button', { name: /set up strategy/i }))
        const targetInput = screen.getByLabelText(/exploration publication target/i)
        await user.clear(targetInput)
        await user.type(targetInput, '0')
        expect(screen.getByRole('button', { name: /save as new version/i })).toBeDisabled()
      })

      it('closes on cancel without saving', async () => {
        const { user } = renderChannels()
        await waitFor(() => screen.getByRole('button', { name: /set up strategy/i }))
        await user.click(screen.getByRole('button', { name: /set up strategy/i }))
        await user.click(screen.getByRole('button', { name: /^cancel$/i }))
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      })

      it('never shows a topic whitelist input', async () => {
        const { user } = renderChannels()
        await waitFor(() => screen.getByRole('button', { name: /set up strategy/i }))
        await user.click(screen.getByRole('button', { name: /set up strategy/i }))
        expect(screen.queryByLabelText(/topic/i)).not.toBeInTheDocument()
      })
    })
  })

  describe('autonomy readiness', () => {
    it('shows not-ready state by default (mock has one check false)', async () => {
      renderChannels()
      await waitFor(() => screen.getByText('Not ready'))
      expect(screen.getByText('Not authorized')).toBeInTheDocument()
    })

    it('shows ready-for-decision-automation without implying publishing is authorized', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/readiness`, () =>
          HttpResponse.json(autonomyReadinessResponse),
        ),
      )
      renderChannels()
      await waitFor(() => screen.getByText('Strategy profile active'))
      // 'Ready' now appears on the decision-automation card and on every
      // ready category row, so target the card itself rather than the word.
      const decisionCard = screen.getByText('Decision automation').closest('.metric-card')
      expect(decisionCard).toHaveTextContent('Ready')
      expect(screen.getByText('Not authorized')).toBeInTheDocument()
    })

    it('lists each readiness check with its detail text', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/readiness`, () =>
          HttpResponse.json(autonomyReadinessResponse),
        ),
      )
      renderChannels()
      await waitFor(() => screen.getByText('Strategy profile active'))
      expect(screen.getByText('Active strategy profile v1')).toBeInTheDocument()
      expect(screen.getByText('Eligible opportunities available')).toBeInTheDocument()
    })

    it('groups checks under their category with a rolled-up status', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/readiness`, () =>
          HttpResponse.json(autonomyReadinessResponse),
        ),
      )
      renderChannels()
      await waitFor(() => screen.getByText('Decision readiness'))
      for (const label of [
        'Production readiness',
        'Analytics & learning readiness',
        'OAuth / provider readiness',
        'Autonomous public publishing',
        'Scheduler health',
      ]) {
        expect(screen.getByText(label)).toBeInTheDocument()
      }
      // A degraded category must read as degraded, not as a plain failure —
      // bootstrap-immature learning evidence is expected, not broken.
      const analytics = screen.getByText('Analytics & learning readiness').closest('.card')
      expect(analytics).toHaveTextContent('Degraded')
    })

    it('reports publishing authorization the right way round', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/readiness`, () =>
          HttpResponse.json({
            ...autonomyReadinessResponse,
            authorized_for_public_publishing: true,
            overall_status: 'ready',
            checks: autonomyReadinessResponse.checks.map(c =>
              c.key === 'public_publishing_authorized'
                ? { ...c, ready: true, status: 'ready' as const, detail: 'Authorized — 0/1 publications in the last 24h' }
                : c,
            ),
            categories: autonomyReadinessResponse.categories.map(c =>
              c.key === 'publishing_authorization' ? { ...c, status: 'ready' as const } : c,
            ),
          }),
        ),
      )
      renderChannels()
      // An authorized channel must show as authorized. The pre-18D check was
      // inverted and went red exactly when a channel became correctly
      // authorized to publish.
      await waitFor(() => screen.getByText('Authorized'))
      expect(screen.getByText('Autonomous public publishing authorized')).toBeInTheDocument()
      expect(screen.queryByText('Not authorized')).not.toBeInTheDocument()
    })
  })

  describe('automation & publishing policy', () => {
    it('shows empty state when no automation policy is configured', async () => {
      renderChannels()
      await waitFor(() => screen.getByText(/decision automation is not configured/i))
      expect(screen.getByRole('button', { name: /set up automation/i })).toBeInTheDocument()
    })

    describe('with a configured policy', () => {
      function usePolicy() {
        server.use(
          http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/automation-policy`, () =>
            HttpResponse.json(channelAutomationPolicyResponse),
          ),
        )
      }

      it('shows decision automation state, cadence, and queue depth', async () => {
        usePolicy()
        renderChannels()
        await waitFor(() => expect(screen.getAllByText('Enabled').length).toBeGreaterThan(0))
        expect(screen.getByText('Daily')).toBeInTheDocument()
        expect(screen.getByText('1 / 1')).toBeInTheDocument()
      })

      it('shows production automation as a distinct state from decision automation', async () => {
        usePolicy()
        renderChannels()
        await waitFor(() => screen.getByText('Production automation'))
        const card = screen.getByText('Production automation').closest('.metric-card')
        expect(card).toHaveTextContent('Enabled')
      })

      it('shows the queued candidate and last decision outcome', async () => {
        usePolicy()
        renderChannels()
        await waitFor(() => screen.getByText('Opportunity #7'))
        expect(screen.getByText(/selected/i)).toBeInTheDocument()
      })

      it('keeps public publishing authorization visibly separate and not authorized', async () => {
        usePolicy()
        renderChannels()
        await waitFor(() => screen.getByText('Public publishing authorization'))
        expect(screen.getByText('Public publishing authorization').closest('.card'))
          .toHaveTextContent('Not authorized')
      })

      it('offers an "Edit automation policy" action', async () => {
        usePolicy()
        renderChannels()
        await waitFor(() => screen.getByRole('button', { name: /edit automation policy/i }))
      })
    })

    describe('public publishing authorization', () => {
      function usePolicy() {
        server.use(
          http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/automation-policy`, () =>
            HttpResponse.json(channelAutomationPolicyResponse),
          ),
        )
      }

      it('explains what is blocking publishing rather than showing a bare "no"', async () => {
        usePolicy()
        renderChannels()
        // Wait for the live authorization decision, not just the card heading —
        // the "waiting on" detail only renders once that query resolves.
        await waitFor(() => screen.getByText(/waiting on/i))
        const card = screen.getByText('Public publishing authorization').closest('.card')!
        expect(card).toHaveTextContent('Not authorized')
        expect(card).toHaveTextContent(/system-wide publishing is switched off/i)
        expect(card).toHaveTextContent(/this channel is not authorized to publish/i)
      })

      it('shows the daily publication ceiling', async () => {
        usePolicy()
        renderChannels()
        await waitFor(() => screen.getByText(/published in the last 24 hours/i))
        expect(screen.getByText('0 of 1 allowed')).toBeInTheDocument()
      })

      it('requires typing AUTHORIZE before the confirm button enables', async () => {
        usePolicy()
        const { user } = renderChannels()
        await waitFor(() => screen.getByRole('button', { name: /authorize…/i }))
        await user.click(screen.getByRole('button', { name: /authorize…/i }))
        await waitFor(() => screen.getByRole('dialog'))

        const confirmBtn = screen.getByRole('button', { name: /^authorize publishing$/i })
        expect(confirmBtn).toBeDisabled()

        await user.type(screen.getByLabelText(/type AUTHORIZE to confirm/i), 'AUTHORIZE')
        expect(confirmBtn).toBeEnabled()
      })

      it('states plainly that videos will publish without per-video review', async () => {
        usePolicy()
        const { user } = renderChannels()
        await waitFor(() => screen.getByRole('button', { name: /authorize…/i }))
        await user.click(screen.getByRole('button', { name: /authorize…/i }))
        await waitFor(() => screen.getByRole('dialog'))

        expect(screen.getByRole('dialog')).toHaveTextContent(
          /publish videos to the public without asking first/i,
        )
      })

      it('is not reachable from the ordinary automation policy form', async () => {
        usePolicy()
        const { user } = renderChannels()
        await waitFor(() => screen.getByRole('button', { name: /edit automation policy/i }))
        await user.click(screen.getByRole('button', { name: /edit automation policy/i }))
        await waitFor(() => screen.getByRole('dialog'))

        const dialog = screen.getByRole('dialog')
        expect(dialog).not.toHaveTextContent(/public publishing authorization/i)
        expect(within(dialog).queryByLabelText(/authorize/i)).toBeNull()
      })

      it('shows who authorized it once granted', async () => {
        usePolicy()
        server.use(
          http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/publishing-authorization`, () =>
            HttpResponse.json(publishingAuthorizationAuthorized),
          ),
        )
        renderChannels()
        await waitFor(() => screen.getByRole('button', { name: /turn off/i }))
        const card = screen.getByText('Public publishing authorization').closest('.card')!
        expect(card).toHaveTextContent('Authorized')
        expect(card).toHaveTextContent(/by dev:studio-user/i)
        expect(within(card as HTMLElement).getByRole('button', { name: /turn off/i }))
          .toBeInTheDocument()
      })
    })

    describe('editing', () => {
      it('opens the edit modal and requires a timezone before enabling automation', async () => {
        const { user } = renderChannels()
        await waitFor(() => screen.getByRole('button', { name: /set up automation/i }))
        await user.click(screen.getByRole('button', { name: /set up automation/i }))
        await waitFor(() => screen.getByRole('dialog'))

        await user.click(screen.getByLabelText(/decision automation enabled/i))
        await waitFor(() => screen.getByText(/timezone is required/i))
        expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled()
      })

      it('submits the policy update with the entered timezone', async () => {
        const { user } = renderChannels()
        await waitFor(() => screen.getByRole('button', { name: /set up automation/i }))
        await user.click(screen.getByRole('button', { name: /set up automation/i }))
        await waitFor(() => screen.getByRole('dialog'))

        await user.click(screen.getByLabelText(/decision automation enabled/i))
        await user.type(screen.getByLabelText(/timezone/i), 'America/New_York')
        expect(screen.getByRole('button', { name: /^save$/i })).not.toBeDisabled()
        await user.click(screen.getByRole('button', { name: /^save$/i }))

        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      })
    })
  })

  describe('empty state', () => {
    it('shows empty state when no channels exist', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels`, () => HttpResponse.json([])),
      )
      renderChannels()
      await waitFor(() => screen.getByText(/no channel yet/i))
    })
  })
})

describe('Create Channel', () => {
  it('shows create channel action in empty state', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/channels`, () => HttpResponse.json([])),
    )
    const { user } = renderChannels()
    await waitFor(() => screen.getByText(/no channel yet/i))
    const createBtn = screen.getAllByRole('button').find(b => b.textContent?.includes('New channel'))
    expect(createBtn).toBeDefined()
    await user.click(createBtn!)
    expect(screen.getByRole('dialog', { name: /new channel/i })).toBeInTheDocument()
  })

  it('auto-derives slug from name', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/channels`, () => HttpResponse.json([])),
    )
    const { user } = renderChannels()
    await waitFor(() => screen.getByText(/no channel yet/i))
    await user.click(screen.getAllByRole('button').find(b => b.textContent?.includes('New channel'))!)
    await user.type(screen.getByLabelText(/channel name/i), 'My New Channel')
    expect(screen.getByLabelText(/^slug/i)).toHaveValue('my-new-channel')
  })

  it('submit button is disabled when name is empty', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/channels`, () => HttpResponse.json([])),
    )
    const { user } = renderChannels()
    await waitFor(() => screen.getByText(/no channel yet/i))
    await user.click(screen.getAllByRole('button').find(b => b.textContent?.includes('New channel'))!)
    expect(screen.getByRole('button', { name: /create channel/i })).toBeDisabled()
  })
})

describe('Add Platform Account', () => {
  it('shows add account button in YouTube accounts section', async () => {
    renderChannels()
    await waitFor(() => screen.getByText(/add account/i))
    expect(screen.getByRole('button', { name: /add account/i })).toBeInTheDocument()
  })

  it('opens modal when add account is clicked', async () => {
    const { user } = renderChannels()
    await waitFor(() => screen.getByRole('button', { name: /add account/i }))
    await user.click(screen.getByRole('button', { name: /add account/i }))
    expect(screen.getByRole('dialog', { name: /register platform account/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/external account id/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/display name/i)).toBeInTheDocument()
  })

  it('submit is disabled when fields are empty', async () => {
    const { user } = renderChannels()
    await waitFor(() => screen.getByRole('button', { name: /add account/i }))
    await user.click(screen.getByRole('button', { name: /add account/i }))
    expect(screen.getByRole('button', { name: /register account/i })).toBeDisabled()
  })

  it('submit enabled when external id and display name filled', async () => {
    const { user } = renderChannels()
    await waitFor(() => screen.getByRole('button', { name: /add account/i }))
    await user.click(screen.getByRole('button', { name: /add account/i }))
    await user.type(screen.getByLabelText(/external account id/i), 'UCtest123')
    await user.type(screen.getByLabelText(/display name/i), 'Test Channel')
    expect(screen.getByRole('button', { name: /register account/i })).toBeEnabled()
  })

  it('closes modal on Cancel', async () => {
    const { user } = renderChannels()
    await waitFor(() => screen.getByRole('button', { name: /add account/i }))
    await user.click(screen.getByRole('button', { name: /add account/i }))
    await user.click(screen.getByRole('button', { name: /^cancel$/i }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('modal contains truthful disclaimer about credential state', async () => {
    const { user } = renderChannels()
    await waitFor(() => screen.getByRole('button', { name: /add account/i }))
    await user.click(screen.getByRole('button', { name: /add account/i }))
    expect(screen.getByText(/registers account metadata only/i)).toBeInTheDocument()
    expect(screen.getByText(/disconnected/i)).toBeInTheDocument()
  })
})

// ── Phase 18C follow-up: upload vs public-release permission ────────────────

describe('YouTube permission model', () => {
  /** Orvella's real credential shape: upload and analytics granted, release not. */
  function connectedWith({ release }: { release: boolean }) {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts/:accountId/connection`, () =>
        HttpResponse.json({
          account_id: 'mock',
          connected: true,
          provider_channel_id: 'UCtest',
          channel_title: 'Test Channel',
          verified_at: null,
          granted_scopes: [],
          upload_scope_granted: true,
          analytics_scope_granted: true,
          release_scope_granted: release,
          credential_status: 'active',
          health_status: 'healthy',
        }),
      ),
    )
  }

  it('shows upload granted and public release NOT granted, distinctly', async () => {
    connectedWith({ release: false })
    renderChannels()
    await waitFor(() => screen.getAllByText('Upload videos'))

    const uploadCard = screen.getAllByText('Upload videos')[0].closest('.card')!
    expect(uploadCard).toHaveTextContent('Granted')

    const releaseCard = screen.getAllByText('Make videos public')[0].closest('.card')!
    expect(releaseCard).toHaveTextContent('Not granted')
  })

  it('never labels a single permission as covering both upload and release', async () => {
    connectedWith({ release: false })
    renderChannels()
    await waitFor(() => screen.getAllByText('Upload videos'))
    // The old ambiguous label must be gone — it implied release was granted.
    expect(screen.queryByText('Publishing')).not.toBeInTheDocument()
  })

  it('shows the release upgrade button only while the permission is missing', async () => {
    connectedWith({ release: false })
    renderChannels()
    await waitFor(() =>
      expect(
        screen.getAllByRole('button', { name: /enable public release permission/i }).length,
      ).toBeGreaterThan(0),
    )
  })

  it('hides the release upgrade button once the permission is granted', async () => {
    connectedWith({ release: true })
    renderChannels()
    await waitFor(() => screen.getAllByText('Make videos public'))
    const releaseCard = screen.getAllByText('Make videos public')[0].closest('.card')!
    expect(releaseCard).toHaveTextContent('Granted')
    expect(
      screen.queryByRole('button', { name: /enable public release permission/i }),
    ).not.toBeInTheDocument()
  })

  it('calls the canonical upgrade-release endpoint and follows the returned URL', async () => {
    connectedWith({ release: false })

    let hitUrl: string | null = null
    server.use(
      http.post(
        `${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts/:accountId/oauth/youtube/upgrade-release`,
        ({ request }) => {
          hitUrl = request.url
          return HttpResponse.json({
            authorization_url: 'https://accounts.google.com/o/oauth2/auth?state=release-live',
          })
        },
      ),
    )

    // The hook navigates via window.location.href; capture instead of navigating.
    const originalLocation = window.location
    let navigatedTo: string | null = null
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...originalLocation, set href(v: string) { navigatedTo = v } },
    })

    try {
      const { user } = renderChannels()
      await waitFor(() =>
        screen.getAllByRole('button', { name: /enable public release permission/i }),
      )
      await user.click(
        screen.getAllByRole('button', { name: /enable public release permission/i })[0],
      )
      await waitFor(() => expect(navigatedTo).not.toBeNull())
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: originalLocation })
    }

    expect(hitUrl).toContain('/oauth/youtube/upgrade-release')
    expect(navigatedTo).toBe('https://accounts.google.com/o/oauth2/auth?state=release-live')
  })

  it('upload permission alone never satisfies release readiness', async () => {
    connectedWith({ release: false })
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/automation-policy`, () =>
        HttpResponse.json(channelAutomationPolicyResponse),
      ),
    )
    renderChannels()
    await waitFor(() => screen.getByText(/waiting on/i))
    // The publishing decision must still report the missing release scope even
    // though the account can upload.
    expect(screen.getByText(/cannot yet make videos public/i)).toBeInTheDocument()
  })

  it('surfaces the missing release scope as an autonomous-publishing blocker', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/automation-policy`, () =>
        HttpResponse.json(channelAutomationPolicyResponse),
      ),
    )
    renderChannels()
    await waitFor(() => screen.getByText(/waiting on/i))
    const card = screen.getByText('Public publishing authorization').closest('.card')!
    expect(card).toHaveTextContent(/cannot yet make videos public/i)
  })

  it('clears the release blocker once the scope is granted', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/automation-policy`, () =>
        HttpResponse.json(channelAutomationPolicyResponse),
      ),
      http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/publishing-authorization`, () =>
        HttpResponse.json(publishingAuthorizationAuthorized),
      ),
    )
    renderChannels()
    await waitFor(() => screen.getByRole('button', { name: /turn off/i }))
    const card = screen.getByText('Public publishing authorization').closest('.card')!
    expect(card).not.toHaveTextContent(/cannot yet make videos public/i)
  })

  it('the OAuth upgrade never mutates publishing authorization', async () => {
    connectedWith({ release: false })
    let authorizationMutations = 0
    server.use(
      http.put(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/publishing-authorization`, () => {
        authorizationMutations += 1
        return HttpResponse.json({})
      }),
      http.post(
        `${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts/:accountId/oauth/youtube/upgrade-release`,
        () => HttpResponse.json({ authorization_url: 'https://accounts.google.com/x' }),
      ),
    )

    const originalLocation = window.location
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...originalLocation, set href(_v: string) { /* swallow */ } },
    })
    try {
      const { user } = renderChannels()
      await waitFor(() =>
        screen.getAllByRole('button', { name: /enable public release permission/i }),
      )
      await user.click(
        screen.getAllByRole('button', { name: /enable public release permission/i })[0],
      )
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: originalLocation })
    }

    expect(authorizationMutations).toBe(0)
  })
})
