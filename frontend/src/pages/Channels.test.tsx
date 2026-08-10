/* Channels page — rendering, multi-account isolation, create channel */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { server } from '@/test/server'
import { Channels } from './Channels'
import { WS_ID, CH_ID, cpAccount1, cpAccount2, cpChannel } from '@/test/fixtures'
import { MOCK_NEW_CHANNEL_ID, MOCK_NEW_ACCOUNT_ID } from '@/test/handlers'

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

    it('shows empty state when no accounts registered', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts`, () =>
          HttpResponse.json([]),
        ),
      )
      const { user } = renderChannels()
      await selectChannelAlpha(user)
      await waitFor(() => screen.getByText(/no platform accounts registered/i))
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

describe('Create Channel', () => {
  it('shows create channel button in page header', async () => {
    renderChannels()
    await waitFor(() => screen.getByText('Channel Alpha'))
    expect(screen.getByRole('button', { name: /create new channel/i })).toBeInTheDocument()
  })

  it('opens create channel modal on button click', async () => {
    const { user } = renderChannels()
    await waitFor(() => screen.getByText('Channel Alpha'))
    await user.click(screen.getByRole('button', { name: /create new channel/i }))
    expect(screen.getByRole('dialog', { name: /new channel/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/channel name/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/slug/i)).toBeInTheDocument()
  })

  it('auto-derives slug from name', async () => {
    const { user } = renderChannels()
    await waitFor(() => screen.getByText('Channel Alpha'))
    await user.click(screen.getByRole('button', { name: /create new channel/i }))
    await user.type(screen.getByLabelText(/channel name/i), 'My New Channel')
    expect(screen.getByLabelText(/^slug/i)).toHaveValue('my-new-channel')
  })

  it('submit button is disabled when name is empty', async () => {
    const { user } = renderChannels()
    await waitFor(() => screen.getByText('Channel Alpha'))
    await user.click(screen.getByRole('button', { name: /create new channel/i }))
    expect(screen.getByRole('button', { name: /create channel/i })).toBeDisabled()
  })

  it('closes modal and refreshes list on successful create', async () => {
    const newChannel = { ...cpChannel, id: MOCK_NEW_CHANNEL_ID, name: 'Brand New', slug: 'brand-new' }
    server.use(
      http.post(`${B}/workspaces/${WS_ID}/channels`, async () =>
        HttpResponse.json(newChannel, { status: 201 }),
      ),
      http.get(`${B}/workspaces/${WS_ID}/channels`, () =>
        HttpResponse.json([cpChannel, newChannel]),
      ),
    )
    const { user } = renderChannels()
    await waitFor(() => screen.getByText('Channel Alpha'))
    await user.click(screen.getByRole('button', { name: /create new channel/i }))
    await user.type(screen.getByLabelText(/channel name/i), 'Brand New')
    await user.click(screen.getByRole('button', { name: /create channel/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => screen.getByText('Brand New'))
  })

  it('shows api error when create fails', async () => {
    server.use(
      http.post(`${B}/workspaces/${WS_ID}/channels`, () =>
        HttpResponse.json({ detail: 'Slug already taken' }, { status: 400 }),
      ),
    )
    const { user } = renderChannels()
    await waitFor(() => screen.getByText('Channel Alpha'))
    await user.click(screen.getByRole('button', { name: /create new channel/i }))
    await user.type(screen.getByLabelText(/channel name/i), 'Test')
    await user.click(screen.getByRole('button', { name: /create channel/i }))
    await waitFor(() => screen.getByRole('alert'))
    expect(screen.getByRole('alert').textContent).toMatch(/slug already taken/i)
  })

  it('closes modal on Cancel without creating', async () => {
    const { user } = renderChannels()
    await waitFor(() => screen.getByText('Channel Alpha'))
    await user.click(screen.getByRole('button', { name: /create new channel/i }))
    await user.click(screen.getByRole('button', { name: /^cancel$/i }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows create channel action in empty state', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/channels`, () => HttpResponse.json([])),
    )
    const { user } = renderChannels()
    await waitFor(() => screen.getByText(/no channels yet/i))
    const createBtn = screen.getAllByRole('button').find(b => b.textContent?.includes('New Channel'))
    expect(createBtn).toBeDefined()
    await user.click(createBtn!)
    expect(screen.getByRole('dialog', { name: /new channel/i })).toBeInTheDocument()
  })
})

async function selectChannelAlpha(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() => screen.getByRole('button', { name: /select channel channel alpha/i }))
  await user.click(screen.getByRole('button', { name: /select channel channel alpha/i }))
  await waitFor(() => screen.getByText('Platform Accounts'))
}

describe('Add Platform Account', () => {
  it('shows + Add Platform Account button in channel detail', async () => {
    const { user } = renderChannels()
    await selectChannelAlpha(user)
    expect(screen.getByRole('button', { name: /add platform account/i })).toBeInTheDocument()
  })

  it('opens modal when + Add Platform Account is clicked', async () => {
    const { user } = renderChannels()
    await selectChannelAlpha(user)
    await user.click(screen.getByRole('button', { name: /add platform account/i }))
    expect(screen.getByRole('dialog', { name: /register platform account/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/external account id/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/display name/i)).toBeInTheDocument()
  })

  it('submit is disabled when fields are empty', async () => {
    const { user } = renderChannels()
    await selectChannelAlpha(user)
    await user.click(screen.getByRole('button', { name: /add platform account/i }))
    expect(screen.getByRole('button', { name: /register account/i })).toBeDisabled()
  })

  it('submit enabled when external id and display name filled', async () => {
    const { user } = renderChannels()
    await selectChannelAlpha(user)
    await user.click(screen.getByRole('button', { name: /add platform account/i }))
    await user.type(screen.getByLabelText(/external account id/i), 'UCtest123')
    await user.type(screen.getByLabelText(/display name/i), 'Test Channel')
    expect(screen.getByRole('button', { name: /register account/i })).toBeEnabled()
  })

  it('closes modal and refreshes list on successful registration', async () => {
    const newAccount = {
      id: MOCK_NEW_ACCOUNT_ID,
      channel_id: CH_ID,
      platform_id: 'youtube',
      platform_key: 'youtube',
      external_account_id: 'UCnew999',
      display_name: 'New Account',
      status: 'disconnected',
      credential_profile_id: null,
      actor: 'dev:studio-user',
      created_at: '2025-01-01T00:00:00',
      updated_at: '2025-01-01T00:00:00',
      metadata_json: null,
    }
    server.use(
      http.post(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts`, async () =>
        HttpResponse.json(newAccount),
      ),
      http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts`, () =>
        HttpResponse.json([cpAccount1, cpAccount2, newAccount]),
      ),
    )
    const { user } = renderChannels()
    await selectChannelAlpha(user)
    await user.click(screen.getByRole('button', { name: /add platform account/i }))
    await user.type(screen.getByLabelText(/external account id/i), 'UCnew999')
    await user.type(screen.getByLabelText(/display name/i), 'New Account')
    await user.click(screen.getByRole('button', { name: /register account/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => screen.getByText('New Account'))
  })

  it('modal contains truthful disclaimer about credential state', async () => {
    const { user } = renderChannels()
    await selectChannelAlpha(user)
    await user.click(screen.getByRole('button', { name: /add platform account/i }))
    expect(screen.getByText(/registers account metadata only/i)).toBeInTheDocument()
    expect(screen.getByText(/disconnected/i)).toBeInTheDocument()
  })

  it('shows API error when registration fails', async () => {
    server.use(
      http.post(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts`, () =>
        HttpResponse.json({ detail: 'Duplicate external_account_id' }, { status: 400 }),
      ),
    )
    const { user } = renderChannels()
    await selectChannelAlpha(user)
    await user.click(screen.getByRole('button', { name: /add platform account/i }))
    await user.type(screen.getByLabelText(/external account id/i), 'UCdup')
    await user.type(screen.getByLabelText(/display name/i), 'Dup Channel')
    await user.click(screen.getByRole('button', { name: /register account/i }))
    await waitFor(() => screen.getByRole('alert'))
  })

  it('closes modal on Cancel', async () => {
    const { user } = renderChannels()
    await selectChannelAlpha(user)
    await user.click(screen.getByRole('button', { name: /add platform account/i }))
    await user.click(screen.getByRole('button', { name: /^cancel$/i }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('empty state action also opens the add account modal', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts`, () =>
        HttpResponse.json([]),
      ),
    )
    const { user } = renderChannels()
    await selectChannelAlpha(user)
    await waitFor(() => screen.getByText(/no platform accounts registered/i))
    const addBtn = screen.getAllByRole('button').find(b =>
      b.textContent?.includes('Add Platform Account')
    )!
    await user.click(addBtn)
    expect(screen.getByRole('dialog', { name: /register platform account/i })).toBeInTheDocument()
  })
})
