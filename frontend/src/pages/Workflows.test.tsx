/* Workflows — schedules list, create schedule modal */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { server } from '@/test/server'
import { Workflows } from './Workflows'
import { WS_ID } from '@/test/fixtures'
import { MOCK_NEW_SCHEDULE_ID } from '@/test/handlers'

const B = 'http://localhost:5173/api/v1'

const MOCK_SCHEDULE = {
  id: 'sched-001',
  workspace_id: WS_ID,
  channel_id: null,
  name: 'Daily publish',
  operation_type: 'start_pipeline',
  schedule_type: 'cron',
  schedule_config: { cron: '0 9 * * *' },
  timezone: 'UTC',
  is_active: true,
  last_run_at: null,
  next_run_at: '2025-01-02T09:00:00',
  actor: 'cli',
  created_at: '2025-01-01T00:00:00',
  updated_at: '2025-01-01T00:00:00',
}

function renderWorkflows(wsId = WS_ID) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return {
    user: userEvent.setup(),
    ...render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/workspaces/${wsId}/workflows`]}>
          <Routes>
            <Route path="/workspaces/:workspaceId/workflows" element={<Workflows />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  }
}

describe('Workflows — schedules list', () => {
  it('shows + New Schedule button in page header', async () => {
    renderWorkflows()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /new schedule/i })).toBeInTheDocument()
    )
  })

  it('shows schedules table when data is present', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/schedules`, () =>
        HttpResponse.json([MOCK_SCHEDULE]),
      ),
    )
    renderWorkflows()
    await waitFor(() => screen.getByText('Daily publish'))
    expect(screen.getByText('cron')).toBeInTheDocument()
    expect(screen.getByText('start_pipeline')).toBeInTheDocument()
  })

  it('shows empty state when no schedules exist', async () => {
    renderWorkflows()
    await waitFor(() => screen.getByText('No schedules defined'))
  })

  it('empty state has + New Schedule action button', async () => {
    renderWorkflows()
    await waitFor(() => screen.getByText('No schedules defined'))
    const buttons = screen.getAllByRole('button')
    const scheduleBtn = buttons.find(b => b.textContent?.includes('New Schedule'))
    expect(scheduleBtn).toBeDefined()
  })
})

describe('Create Schedule', () => {
  it('opens modal when + New Schedule button is clicked', async () => {
    const { user } = renderWorkflows()
    await waitFor(() => screen.getByRole('button', { name: /new schedule/i }))
    await user.click(screen.getByRole('button', { name: /new schedule/i }))
    expect(screen.getByRole('dialog', { name: /new schedule/i })).toBeInTheDocument()
  })

  it('modal has schedule name input with label', async () => {
    const { user } = renderWorkflows()
    await waitFor(() => screen.getByRole('button', { name: /new schedule/i }))
    await user.click(screen.getByRole('button', { name: /new schedule/i }))
    expect(screen.getByLabelText(/schedule name/i)).toBeInTheDocument()
  })

  it('submit button is disabled when name is empty', async () => {
    const { user } = renderWorkflows()
    await waitFor(() => screen.getByRole('button', { name: /new schedule/i }))
    await user.click(screen.getByRole('button', { name: /new schedule/i }))
    expect(screen.getByRole('button', { name: /create schedule/i })).toBeDisabled()
  })

  it('submit button is enabled when name is filled', async () => {
    const { user } = renderWorkflows()
    await waitFor(() => screen.getByRole('button', { name: /new schedule/i }))
    await user.click(screen.getByRole('button', { name: /new schedule/i }))
    await user.type(screen.getByLabelText(/schedule name/i), 'My Schedule')
    expect(screen.getByRole('button', { name: /create schedule/i })).toBeEnabled()
  })

  it('closes modal on Cancel', async () => {
    const { user } = renderWorkflows()
    await waitFor(() => screen.getByRole('button', { name: /new schedule/i }))
    await user.click(screen.getByRole('button', { name: /new schedule/i }))
    await user.click(screen.getByRole('button', { name: /^cancel$/i }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows JSON config validation error on blur', async () => {
    const { user } = renderWorkflows()
    await waitFor(() => screen.getByRole('button', { name: /new schedule/i }))
    await user.click(screen.getByRole('button', { name: /new schedule/i }))
    const configArea = screen.getByLabelText(/schedule config/i)
    await user.clear(configArea)
    await user.type(configArea, 'invalid json{{')  // {{ = literal { in userEvent
    await user.tab()
    await waitFor(() => screen.getByText('Must be valid JSON'))
  })

  it('submits and closes modal on success, refreshes list', async () => {
    const newSchedule = { ...MOCK_SCHEDULE, id: MOCK_NEW_SCHEDULE_ID, name: 'Test Schedule' }
    server.use(
      http.post(`${B}/workspaces/${WS_ID}/schedules`, async () =>
        HttpResponse.json(newSchedule),
      ),
      http.get(`${B}/workspaces/${WS_ID}/schedules`, () =>
        HttpResponse.json([newSchedule]),
      ),
    )
    const { user } = renderWorkflows()
    await waitFor(() => screen.getByRole('button', { name: /new schedule/i }))
    await user.click(screen.getByRole('button', { name: /new schedule/i }))
    await user.type(screen.getByLabelText(/schedule name/i), 'Test Schedule')
    await user.click(screen.getByRole('button', { name: /create schedule/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => screen.getByText('Test Schedule'))
  })

  it('shows API error when creation fails', async () => {
    server.use(
      http.post(`${B}/workspaces/${WS_ID}/schedules`, () =>
        HttpResponse.json({ detail: 'Schedule name already exists' }, { status: 400 }),
      ),
    )
    const { user } = renderWorkflows()
    await waitFor(() => screen.getByRole('button', { name: /new schedule/i }))
    await user.click(screen.getByRole('button', { name: /new schedule/i }))
    await user.type(screen.getByLabelText(/schedule name/i), 'Dup Schedule')
    await user.click(screen.getByRole('button', { name: /create schedule/i }))
    await waitFor(() => screen.getByRole('alert'))
  })

  it('opens modal from empty state action button', async () => {
    const { user } = renderWorkflows()
    await waitFor(() => screen.getByText('No schedules defined'))
    const buttons = screen.getAllByRole('button')
    const scheduleBtn = buttons.find(b => b.textContent?.includes('New Schedule'))!
    await user.click(scheduleBtn)
    expect(screen.getByRole('dialog', { name: /new schedule/i })).toBeInTheDocument()
  })
})

describe('Schedule Actions — Pause / Resume / Delete', () => {
  it('active schedule shows Pause button', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/schedules`, () =>
        HttpResponse.json([MOCK_SCHEDULE]),
      ),
    )
    renderWorkflows()
    await waitFor(() => screen.getByText('Daily publish'))
    expect(screen.getByRole('button', { name: /pause schedule Daily publish/i })).toBeInTheDocument()
  })

  it('inactive schedule shows Resume button', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/schedules`, () =>
        HttpResponse.json([{ ...MOCK_SCHEDULE, is_active: false }]),
      ),
    )
    renderWorkflows()
    await waitFor(() => screen.getByText('Daily publish'))
    expect(screen.getByRole('button', { name: /resume schedule Daily publish/i })).toBeInTheDocument()
  })

  it('Delete button is visible for each schedule', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/schedules`, () =>
        HttpResponse.json([MOCK_SCHEDULE]),
      ),
    )
    renderWorkflows()
    await waitFor(() => screen.getByText('Daily publish'))
    expect(screen.getByRole('button', { name: /delete schedule Daily publish/i })).toBeInTheDocument()
  })

  it('pause API error shows dismissible alert', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/schedules`, () =>
        HttpResponse.json([MOCK_SCHEDULE]),
      ),
      http.post(`${B}/workspaces/${WS_ID}/schedules/${MOCK_SCHEDULE.id}/pause`, () =>
        HttpResponse.json({ detail: 'Schedule not found' }, { status: 404 }),
      ),
    )
    const { user } = renderWorkflows()
    await waitFor(() => screen.getByText('Daily publish'))
    await user.click(screen.getByRole('button', { name: /pause schedule Daily publish/i }))
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
  })

  it('resume API error shows dismissible alert', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/schedules`, () =>
        HttpResponse.json([{ ...MOCK_SCHEDULE, is_active: false }]),
      ),
      http.post(`${B}/workspaces/${WS_ID}/schedules/${MOCK_SCHEDULE.id}/resume`, () =>
        HttpResponse.json({ detail: 'Conflict' }, { status: 409 }),
      ),
    )
    const { user } = renderWorkflows()
    await waitFor(() => screen.getByText('Daily publish'))
    await user.click(screen.getByRole('button', { name: /resume schedule Daily publish/i }))
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
  })
})
