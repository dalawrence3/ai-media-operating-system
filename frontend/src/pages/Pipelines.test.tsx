/* Pipeline Studio — stage rendering, status states, recovery actions */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { server } from '@/test/server'
import { Pipelines } from './Pipelines'
import {
  WS_ID,
  pipelineBlocked, pipelineWaitingReview, pipelineFailed,
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
