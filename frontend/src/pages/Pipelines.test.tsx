/* Pipeline Studio — stage rendering, status states, recovery actions, start pipeline,
   topic management, artifact viewer, diagnostics, manual advance */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { server } from '@/test/server'
import { Pipelines } from './Pipelines'
import {
  WS_ID, CH_ID, PIPE_ID,
  cpChannel,
  pipelineBlocked, pipelineWaitingReview, pipelineFailed, pipelineView,
  topicView, stageArtifactUnresolved, stageDiagnosticReport,
} from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

function renderPipelines(wsId = WS_ID) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return {
    user: userEvent.setup(),
    ...render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/workspaces/${wsId}/pipelines`]}>
          <Routes>
            <Route path="/workspaces/:workspaceId/pipelines" element={<Pipelines />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  }
}

// aria-label is "Pipeline {id.slice(0,8)}" e.g. "Pipeline pipe-tes" for 'pipe-test-001'
async function selectPipeline(user: ReturnType<typeof userEvent.setup>, namePattern: RegExp) {
  const btn = await screen.findByRole('button', { name: namePattern })
  await user.click(btn)
}

describe('Pipelines', () => {
  describe('stage status rendering', () => {
    it('shows pipeline stage bar with aria labels', async () => {
      renderPipelines()
      await waitFor(() => screen.getByRole('list', { name: /pipeline stages/i }))
      expect(screen.getAllByRole('listitem')).toHaveLength(10)
    })

    it('renders completed stage with correct aria label', async () => {
      renderPipelines()
      await waitFor(() => screen.getByRole('listitem', { name: /Research: completed/i }))
    })

    it('renders running stage with correct aria label', async () => {
      renderPipelines()
      await waitFor(() => screen.getByRole('listitem', { name: /Script: running/i }))
    })

    it('renders not-started stage', async () => {
      renderPipelines()
      await waitFor(() => screen.getByRole('listitem', { name: /Rendering: not started/i }))
    })
  })

  describe('pipeline status states', () => {
    it('shows Running status badge in the list', async () => {
      renderPipelines()
      // aria-label is "Pipeline pipe-tes" (id.slice(0,8) of 'pipe-test-001')
      await waitFor(() => screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
      expect(screen.getAllByText('Running').length).toBeGreaterThan(0)
    })

    it('shows blocked pipeline blocked_reason in detail', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/pipelines`, () =>
          HttpResponse.json([pipelineBlocked]),
        ),
        http.get(`${B}/workspaces/${WS_ID}/pipelines/${pipelineBlocked.id}`, () =>
          HttpResponse.json(pipelineBlocked),
        ),
      )
      const { user } = renderPipelines()
      // aria-label is "Pipeline pipe-blo" (id.slice(0,8) of 'pipe-blocked-001')
      await selectPipeline(user, /Pipeline pipe-blo/i)
      await waitFor(() => screen.getByText(/provider_setup_required/))
    })

    it('shows waiting_for_review status as Review Required', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/pipelines`, () =>
          HttpResponse.json([pipelineWaitingReview]),
        ),
      )
      renderPipelines()
      await waitFor(() => screen.getAllByText('Review Required'))
    })

    it('shows failed error message in detail', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/pipelines`, () =>
          HttpResponse.json([pipelineFailed]),
        ),
        http.get(`${B}/workspaces/${WS_ID}/pipelines/${pipelineFailed.id}`, () =>
          HttpResponse.json(pipelineFailed),
        ),
      )
      const { user } = renderPipelines()
      // aria-label is "Pipeline pipe-fai" (id.slice(0,8) of 'pipe-failed-001')
      await selectPipeline(user, /Pipeline pipe-fai/i)
      await waitFor(() => screen.getByText(/quota exceeded/))
    })
  })

  describe('pipeline actions', () => {
    it('shows pause button when running pipeline is selected', async () => {
      const { user } = renderPipelines()
      await selectPipeline(user, /Pipeline pipe-tes/i)
      expect(await screen.findByRole('button', { name: /^pause$/i })).toBeInTheDocument()
    })

    it('shows recover button when failed pipeline is selected', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/pipelines`, () =>
          HttpResponse.json([pipelineFailed]),
        ),
        http.get(`${B}/workspaces/${WS_ID}/pipelines/${pipelineFailed.id}`, () =>
          HttpResponse.json(pipelineFailed),
        ),
      )
      const { user } = renderPipelines()
      await selectPipeline(user, /Pipeline pipe-fai/i)
      expect(await screen.findByRole('button', { name: /^recover$/i })).toBeInTheDocument()
    })
  })

  describe('attempt number', () => {
    it('shows attempt number in stage history table', async () => {
      const { user } = renderPipelines()
      await selectPipeline(user, /Pipeline pipe-tes/i)
      await waitFor(() => screen.getByText('Stage History'))
      const cells = screen.getAllByRole('cell')
      const attemptCells = cells.filter(c => c.textContent === '1')
      expect(attemptCells.length).toBeGreaterThan(0)
    })
  })

  describe('artifact reference', () => {
    it('shows artifact type and truncated id in stage detail', async () => {
      const { user } = renderPipelines()
      await selectPipeline(user, /Pipeline pipe-tes/i)
      await waitFor(() => screen.getByText('Stage History'))
      expect(screen.getByText(/research_brief:art-001/)).toBeInTheDocument()
    })
  })

  describe('empty state', () => {
    it('shows empty state when no pipelines exist', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/pipelines`, () => HttpResponse.json([])),
      )
      renderPipelines()
      await waitFor(() => screen.getByText('No pipelines'))
    })
  })
})

describe('Start Pipeline', () => {
  it('shows Start Pipeline button in page header', async () => {
    renderPipelines()
    await waitFor(() => screen.getByRole('button', { name: /start pipeline/i }))
  })

  it('opens start pipeline modal on button click', async () => {
    const { user } = renderPipelines()
    await waitFor(() => screen.getByRole('button', { name: /start pipeline/i }))
    await user.click(screen.getByRole('button', { name: /start pipeline/i }))
    expect(screen.getByRole('dialog', { name: /start pipeline/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/channel/i)).toBeInTheDocument()
  })

  it('submit button is disabled when no channel selected', async () => {
    const { user } = renderPipelines()
    await waitFor(() => screen.getByRole('button', { name: /start pipeline/i }))
    await user.click(screen.getByRole('button', { name: /start pipeline/i }))
    expect(within(screen.getByRole('dialog')).getByRole('button', { name: /^start pipeline$/i })).toBeDisabled()
  })

  it('closes modal on Cancel', async () => {
    const { user } = renderPipelines()
    await waitFor(() => screen.getByRole('button', { name: /start pipeline/i }))
    await user.click(screen.getByRole('button', { name: /start pipeline/i }))
    await user.click(screen.getByRole('button', { name: /^cancel$/i }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows start pipeline action in empty state', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/pipelines`, () => HttpResponse.json([])),
    )
    const { user } = renderPipelines()
    await waitFor(() => screen.getByText('No pipelines'))
    const startBtn = screen.getAllByRole('button').find(b => b.textContent?.includes('Start Pipeline'))
    expect(startBtn).toBeDefined()
    await user.click(startBtn!)
    expect(screen.getByRole('dialog', { name: /start pipeline/i })).toBeInTheDocument()
  })

  it('submits and refreshes list after successful start', async () => {
    const newPipeline = { ...pipelineView, id: 'pipe-new-001', status: 'pending' }
    server.use(
      http.post(`${B}/workspaces/${WS_ID}/pipelines`, async () =>
        HttpResponse.json(newPipeline),
      ),
      http.get(`${B}/workspaces/${WS_ID}/pipelines`, () =>
        HttpResponse.json([pipelineView, newPipeline]),
      ),
    )
    const { user } = renderPipelines()
    await waitFor(() => screen.getByRole('button', { name: /start pipeline/i }))
    await user.click(screen.getByRole('button', { name: /start pipeline/i }))
    // Select a channel in the dropdown
    const channelSelect = screen.getByLabelText(/channel/i)
    await user.selectOptions(channelSelect, cpChannel.id)
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^start pipeline$/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
})

// ── Topic management ──────────────────────────────────────────────────────────

describe('Start Pipeline — topic management', () => {
  async function openModal(user: ReturnType<typeof userEvent.setup>) {
    await waitFor(() => screen.getByRole('button', { name: /start pipeline/i }))
    await user.click(screen.getByRole('button', { name: /start pipeline/i }))
    await waitFor(() => screen.getByRole('dialog', { name: /start pipeline/i }))
  }

  it('shows topic dropdown populated from API', async () => {
    const { user } = renderPipelines()
    await openModal(user)
    const topicSelect = screen.getByLabelText(/topic/i)
    await waitFor(() => {
      expect(topicSelect).toHaveTextContent('AI in Healthcare')
      expect(topicSelect).toHaveTextContent('Future of Renewable Energy')
    })
  })

  it('shows "No topic (optional)" default option', async () => {
    const { user } = renderPipelines()
    await openModal(user)
    expect(screen.getByRole('option', { name: /no topic \(optional\)/i })).toBeInTheDocument()
  })

  it('shows "Create new topic…" option in topic dropdown', async () => {
    const { user } = renderPipelines()
    await openModal(user)
    expect(screen.getByRole('option', { name: /create new topic/i })).toBeInTheDocument()
  })

  it('shows topic create form when "+ Create new topic…" is selected', async () => {
    const { user } = renderPipelines()
    await openModal(user)
    const topicSelect = screen.getByLabelText(/topic/i)
    await user.selectOptions(topicSelect, '__new__')
    expect(screen.getByLabelText(/new topic title/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/topic angle/i)).toBeInTheDocument()
  })

  it('saves inline topic and populates selection', async () => {
    const { user } = renderPipelines()
    await openModal(user)
    const topicSelect = screen.getByLabelText(/topic/i)
    await user.selectOptions(topicSelect, '__new__')
    await user.type(screen.getByLabelText(/new topic title/i), 'Quantum Computing')
    await user.click(screen.getByRole('button', { name: /save new topic/i }))
    await waitFor(() => expect(screen.queryByLabelText(/new topic title/i)).not.toBeInTheDocument())
  })

  it('discard button hides the create topic form', async () => {
    const { user } = renderPipelines()
    await openModal(user)
    const topicSelect = screen.getByLabelText(/topic/i)
    await user.selectOptions(topicSelect, '__new__')
    await user.click(screen.getByRole('button', { name: /discard/i }))
    expect(screen.queryByLabelText(/new topic title/i)).not.toBeInTheDocument()
  })

  it('shows selected topic name confirmation after selection', async () => {
    const { user } = renderPipelines()
    await openModal(user)
    const topicSelect = screen.getByLabelText(/topic/i)
    await waitFor(() => expect(topicSelect).toHaveTextContent('AI in Healthcare'))
    await user.selectOptions(topicSelect, String(topicView.id))
    await waitFor(() => expect(topicSelect).toHaveValue(String(topicView.id)))
  })

  it('shows API error on create topic failure', async () => {
    server.use(
      http.post(`${B}/workspaces/${WS_ID}/topics`, () =>
        HttpResponse.json({ detail: 'Title already exists' }, { status: 422 }),
      ),
    )
    const { user } = renderPipelines()
    await openModal(user)
    await user.selectOptions(screen.getByLabelText(/topic/i), '__new__')
    await user.type(screen.getByLabelText(/new topic title/i), 'Duplicate Topic')
    await user.click(screen.getByRole('button', { name: /save new topic/i }))
    await waitFor(() => screen.getByRole('alert'))
  })

  it('shows loading state when topics are loading', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/topics`, async () => {
        await new Promise(r => setTimeout(r, 200))
        return HttpResponse.json([topicView])
      }),
    )
    const { user } = renderPipelines()
    await openModal(user)
    expect(screen.getByText(/loading topics/i)).toBeInTheDocument()
  })

  it('submits pipeline with selected topic_id', async () => {
    let capturedBody: Record<string, unknown> = {}
    server.use(
      http.post(`${B}/workspaces/${WS_ID}/pipelines`, async ({ request }) => {
        capturedBody = await request.json() as Record<string, unknown>
        return HttpResponse.json({ ...pipelineView, topic_id: topicView.id })
      }),
    )
    const { user } = renderPipelines()
    await openModal(user)
    await user.selectOptions(screen.getByLabelText(/channel/i), CH_ID)
    const topicSelect = screen.getByLabelText(/topic/i)
    await waitFor(() => expect(topicSelect).toHaveTextContent('AI in Healthcare'))
    await user.selectOptions(topicSelect, String(topicView.id))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^start pipeline$/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(capturedBody.topic_id).toBe(topicView.id)
  })
})

// ── Artifact viewer ───────────────────────────────────────────────────────────

describe('Artifact viewer', () => {
  it('shows artifact content for research stage (resolved)', async () => {
    const { user } = renderPipelines()
    await waitFor(() => screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await user.click(screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await waitFor(() => screen.getByText('Stage History'))
    const expandBtn = screen.getByRole('button', { name: /expand Research/i })
    await user.click(expandBtn)
    await waitFor(() => screen.getByLabelText(/artifact content/i))
    expect(screen.getByLabelText(/artifact content/i)).toBeInTheDocument()
  })

  it('shows "no artifact" label for unresolved stages', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/pipelines/${PIPE_ID}/stages/research/artifact`, () =>
        HttpResponse.json(stageArtifactUnresolved),
      ),
    )
    const { user } = renderPipelines()
    await waitFor(() => screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await user.click(screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await waitFor(() => screen.getByText('Stage History'))
    const expandBtn = screen.getByRole('button', { name: /expand Research/i })
    await user.click(expandBtn)
    await waitFor(() => screen.getByText(/No artifact produced yet/i))
  })

  it('shows artifact unavailable on API error', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/pipelines/${PIPE_ID}/stages/research/artifact`, () =>
        HttpResponse.json({ detail: 'Not found' }, { status: 404 }),
      ),
    )
    const { user } = renderPipelines()
    await waitFor(() => screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await user.click(screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await waitFor(() => screen.getByText('Stage History'))
    const expandBtn = screen.getByRole('button', { name: /expand Research/i })
    await user.click(expandBtn)
    await waitFor(() => screen.getByText(/artifact unavailable/i))
  })

  it('collapses stage row on second click', async () => {
    const { user } = renderPipelines()
    await waitFor(() => screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await user.click(screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await waitFor(() => screen.getByText('Stage History'))
    const expandBtn = screen.getByRole('button', { name: /expand Research/i })
    await user.click(expandBtn)
    await waitFor(() => screen.getByRole('button', { name: /collapse Research/i }))
    await user.click(screen.getByRole('button', { name: /collapse Research/i }))
    expect(screen.queryByLabelText(/artifact content/i)).not.toBeInTheDocument()
  })
})

// ── Diagnostics panel ─────────────────────────────────────────────────────────

describe('Diagnostics panel', () => {
  it('shows diagnostic findings when stage is expanded', async () => {
    const { user } = renderPipelines()
    await waitFor(() => screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await user.click(screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await waitFor(() => screen.getByText('Stage History'))
    await user.click(screen.getByRole('button', { name: /expand Research/i }))
    await waitFor(() => screen.getByText(/Stage running longer than expected/i))
  })

  it('shows diagnostics unavailable on API error', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/pipelines/${PIPE_ID}/diagnostics/research`, () =>
        HttpResponse.json({ detail: 'Service unavailable' }, { status: 503 }),
      ),
    )
    const { user } = renderPipelines()
    await waitFor(() => screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await user.click(screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await waitFor(() => screen.getByText('Stage History'))
    await user.click(screen.getByRole('button', { name: /expand Research/i }))
    await waitFor(() => screen.getByText(/diagnostics unavailable/i))
  })

  it('shows "No diagnostic findings" when findings list is empty', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/pipelines/${PIPE_ID}/diagnostics/research`, () =>
        HttpResponse.json({ ...stageDiagnosticReport, findings: [] }),
      ),
    )
    const { user } = renderPipelines()
    await waitFor(() => screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await user.click(screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await waitFor(() => screen.getByText('Stage History'))
    await user.click(screen.getByRole('button', { name: /expand Research/i }))
    await waitFor(() => screen.getByText(/No diagnostic findings/i))
  })
})

// ── Manual advance ────────────────────────────────────────────────────────────

describe('Manual advance', () => {
  const pipelineReview = {
    ...pipelineView,
    status: 'waiting_for_review',
    stages: [
      {
        stage: 'research',
        attempt_number: 1,
        status: 'waiting_for_review',
        artifact_id: 'art-001',
        artifact_type: 'research_brief',
        error_message: null,
        duration_ms: 1200,
        started_at: '2025-01-01T00:01:00',
        completed_at: null,
      },
    ],
  }

  it('shows advance button for waiting_for_review stage', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/pipelines`, () => HttpResponse.json([pipelineReview])),
      http.get(`${B}/workspaces/${WS_ID}/pipelines/${PIPE_ID}`, () => HttpResponse.json(pipelineReview)),
    )
    const { user } = renderPipelines()
    await waitFor(() => screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await user.click(screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await waitFor(() => screen.getByText('Stage History'))
    await user.click(screen.getByRole('button', { name: /expand Research/i }))
    await waitFor(() => screen.getByRole('button', { name: /manually advance Research/i }))
  })

  it('does not show advance button for completed stage', async () => {
    const { user } = renderPipelines()
    await waitFor(() => screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await user.click(screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await waitFor(() => screen.getByText('Stage History'))
    await user.click(screen.getByRole('button', { name: /expand Research/i }))
    await waitFor(() => screen.queryByRole('button', { name: /manually advance Research/i }))
    expect(screen.queryByRole('button', { name: /manually advance Research/i })).not.toBeInTheDocument()
  })

  it('calls advance endpoint and invalidates query on click', async () => {
    let advanceCalled = false
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/pipelines`, () => HttpResponse.json([pipelineReview])),
      http.get(`${B}/workspaces/${WS_ID}/pipelines/${PIPE_ID}`, () => HttpResponse.json(pipelineReview)),
      http.post(`${B}/workspaces/${WS_ID}/pipelines/${PIPE_ID}/advance`, async () => {
        advanceCalled = true
        return HttpResponse.json({ ...pipelineReview, status: 'running' })
      }),
    )
    const { user } = renderPipelines()
    await waitFor(() => screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await user.click(screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await waitFor(() => screen.getByText('Stage History'))
    await user.click(screen.getByRole('button', { name: /expand Research/i }))
    await waitFor(() => screen.getByRole('button', { name: /manually advance Research/i }))
    await user.click(screen.getByRole('button', { name: /manually advance Research/i }))
    await waitFor(() => expect(advanceCalled).toBe(true))
  })

  it('shows error when advance fails', async () => {
    server.use(
      http.get(`${B}/workspaces/${WS_ID}/pipelines`, () => HttpResponse.json([pipelineReview])),
      http.get(`${B}/workspaces/${WS_ID}/pipelines/${PIPE_ID}`, () => HttpResponse.json(pipelineReview)),
      http.post(`${B}/workspaces/${WS_ID}/pipelines/${PIPE_ID}/advance`, () =>
        HttpResponse.json({ detail: 'Stage not eligible for advance' }, { status: 409 }),
      ),
    )
    const { user } = renderPipelines()
    await waitFor(() => screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await user.click(screen.getByRole('button', { name: /Pipeline pipe-tes/i }))
    await waitFor(() => screen.getByText('Stage History'))
    await user.click(screen.getByRole('button', { name: /expand Research/i }))
    await waitFor(() => screen.getByRole('button', { name: /manually advance Research/i }))
    await user.click(screen.getByRole('button', { name: /manually advance Research/i }))
    await waitFor(() => screen.getByRole('alert'))
  })
})
