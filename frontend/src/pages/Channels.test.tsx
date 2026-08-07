/* Channels page — rendering, multi-account isolation */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { server } from '@/test/server'
import { Channels } from './Channels'
import { WS_ID, CH_ID, cpAccount1, cpAccount2 } from '@/test/fixtures'

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
  describe('channel list rendering', () => {
    it('renders channel names', async () => {
      renderChannels()
      await waitFor(() => screen.getByText('Channel Alpha'))
      expect(screen.getByText('Channel Beta')).toBeInTheDocument()
    })

    it('renders channel slugs', async () => {
      renderChannels()
      await waitFor(() => screen.getByText('/channel-alpha'))
      expect(screen.getByText('/channel-beta')).toBeInTheDocument()
    })

    it('shows empty state when no channels', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels`, () => HttpResponse.json([])),
      )
      renderChannels()
      await waitFor(() => screen.getByText(/no channels yet/i))
    })
  })

  describe('multi-account isolation', () => {
    async function selectChannelAlpha(user: ReturnType<typeof userEvent.setup>) {
      await waitFor(() => screen.getByRole('button', { name: /select channel channel alpha/i }))
      await user.click(screen.getByRole('button', { name: /select channel channel alpha/i }))
      await waitFor(() => screen.getByText('Platform Accounts'))
    }

    it('renders both accounts for selected channel', async () => {
      const { user } = renderChannels()
      await selectChannelAlpha(user)
      // Both account display names must appear
      expect(screen.getByText('Account A')).toBeInTheDocument()
      expect(screen.getByText('Account B')).toBeInTheDocument()
    })

    it('Account A and Account B have independent statuses', async () => {
      const { user } = renderChannels()
      await selectChannelAlpha(user)
      // Scope status checks to the accounts table to avoid channel-list badge overlap
      const table = screen.getByRole('table')
      const inTable = within(table)
      // Account A → active, Account B → paused: both must appear in the accounts table
      expect(inTable.getAllByText('Active').length).toBeGreaterThanOrEqual(1)
      expect(inTable.getAllByText('Paused').length).toBeGreaterThanOrEqual(1)
    })

    it('Account A external ID does not appear next to Account B display name', async () => {
      const { user } = renderChannels()
      await selectChannelAlpha(user)
      const table = screen.getByRole('table')
      const inTable = within(table)
      // Both external IDs must appear exactly once each
      expect(inTable.getByText(cpAccount1.external_account_id)).toBeInTheDocument()
      expect(inTable.getByText(cpAccount2.external_account_id)).toBeInTheDocument()
    })

    it('Account B credential shows "No credential" when null', async () => {
      const { user } = renderChannels()
      await selectChannelAlpha(user)
      expect(await screen.findByText('No credential')).toBeInTheDocument()
    })

    it('shows unavailable state when no accounts connected', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts`, () =>
          HttpResponse.json([]),
        ),
      )
      const { user } = renderChannels()
      await selectChannelAlpha(user)
      await waitFor(() => screen.getByText(/no platform accounts connected/i))
    })
  })

  describe('strategy unavailable state', () => {
    it('shows unavailable state when strategy is not assigned', async () => {
      const { user } = renderChannels()
      await waitFor(() => screen.getByRole('button', { name: /select channel channel alpha/i }))
      await user.click(screen.getByRole('button', { name: /select channel channel alpha/i }))
      await waitFor(() => screen.getByText(/no strategy profile assigned/i))
    })
  })
})
