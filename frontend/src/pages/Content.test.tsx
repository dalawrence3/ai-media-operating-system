/* Content library — Phase 17B.
   Covers: lifecycle filtering, published/publishing/failed content,
   empty states, local date formatting, and detail navigation. */

import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { server } from '@/test/server'
import { Content } from './Content'
import { WS_ID, publicationListItem } from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

function renderContent(wsId = WS_ID) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workspaces/${wsId}/content`]}>
        <Routes>
          <Route path="/workspaces/:workspaceId/content" element={<Content />} />
          <Route path="/workspaces/:workspaceId/content/:publicationId" element={<div>Detail page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const uploading = { ...publicationListItem, id: 2, status: 'uploading', title: 'Draft video', published_at: null }
const failed = { ...publicationListItem, id: 3, status: 'failed', title: 'Failed upload' }
const archived = { ...publicationListItem, id: 4, status: 'deleted', title: 'Removed video' }

describe('Content', () => {
  describe('loading / error', () => {
    it('shows a loading indicator before publications resolve', () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/publications`, async () => {
          await new Promise(r => setTimeout(r, 300))
          return HttpResponse.json([publicationListItem])
        }),
      )
      renderContent()
      expect(screen.getByText(/loading content/i)).toBeInTheDocument()
    })

    it('shows an error state when the list fails to load', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/publications`, () =>
          HttpResponse.json({ detail: 'boom' }, { status: 500 }),
        ),
      )
      renderContent()
      await waitFor(() => screen.getByRole('alert'))
    })
  })

  describe('empty state', () => {
    it('renders an honest empty state with no videos', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/publications`, () => HttpResponse.json([])),
      )
      renderContent()
      await waitFor(() => screen.getByText('No videos yet'))
    })
  })

  describe('lifecycle filtering', () => {
    it('shows all videos under the All tab with correct counts', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/publications`, () =>
          HttpResponse.json([publicationListItem, uploading, failed, archived]),
        ),
      )
      renderContent()
      await waitFor(() => screen.getByText(publicationListItem.title))
      expect(screen.getByRole('tab', { name: /all \(4\)/i })).toBeInTheDocument()
      expect(screen.getByRole('tab', { name: /on youtube \(1\)/i })).toBeInTheDocument()
      expect(screen.getByRole('tab', { name: /publishing \(1\)/i })).toBeInTheDocument()
      expect(screen.getByRole('tab', { name: /failed \(1\)/i })).toBeInTheDocument()
      expect(screen.getByRole('tab', { name: /archived \(1\)/i })).toBeInTheDocument()
    })

    it('filters to published content only', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/publications`, () =>
          HttpResponse.json([publicationListItem, uploading, failed]),
        ),
      )
      const user = userEvent.setup()
      renderContent()
      await waitFor(() => screen.getByText(publicationListItem.title))

      await user.click(screen.getByRole('tab', { name: /on youtube \(1\)/i }))
      expect(screen.getByText(publicationListItem.title)).toBeInTheDocument()
      expect(screen.queryByText('Draft video')).not.toBeInTheDocument()
      expect(screen.queryByText('Failed upload')).not.toBeInTheDocument()
    })

    it('shows in-production content under the Publishing tab', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/publications`, () =>
          HttpResponse.json([publicationListItem, uploading]),
        ),
      )
      const user = userEvent.setup()
      renderContent()
      await waitFor(() => screen.getByText(publicationListItem.title))

      await user.click(screen.getByRole('tab', { name: /publishing \(1\)/i }))
      expect(screen.getByText('Draft video')).toBeInTheDocument()
      expect(screen.queryByText(publicationListItem.title)).not.toBeInTheDocument()
    })

    it('shows an honest empty state for a filter with no matches', async () => {
      server.use(
        http.get(`${B}/workspaces/${WS_ID}/publications`, () =>
          HttpResponse.json([publicationListItem]),
        ),
      )
      const user = userEvent.setup()
      renderContent()
      await waitFor(() => screen.getByText(publicationListItem.title))

      await user.click(screen.getByRole('tab', { name: /failed \(0\)/i }))
      expect(screen.getByText(/no failed videos/i)).toBeInTheDocument()
    })
  })

  describe('video card content', () => {
    it('formats the publish date via local time rather than a raw ISO string', async () => {
      renderContent()
      await waitFor(() => screen.getByText(publicationListItem.title))
      const card = screen.getByText(publicationListItem.title).closest('.video-card') as HTMLElement
      const time = card.querySelector('time')!
      expect(time).toBeTruthy()
      expect(time.getAttribute('dateTime')).toBe(publicationListItem.published_at)
      expect(time.textContent).not.toContain('T')
    })

    it('shows the topic on the card', async () => {
      renderContent()
      await waitFor(() => screen.getByText(publicationListItem.title))
      expect(screen.getByText(publicationListItem.topic_title!)).toBeInTheDocument()
    })

    it('shows duration when available', async () => {
      renderContent()
      await waitFor(() => screen.getByText(publicationListItem.title))
      // render_duration_ms = 58607 -> 58.607s rounds to 0:59 (see formatDurationMs)
      const card = screen.getByText(publicationListItem.title).closest('.video-card') as HTMLElement
      expect(within(card).getByText('0:59')).toBeInTheDocument()
    })
  })

  describe('navigation', () => {
    it('navigates to the detail route when a video card is clicked', async () => {
      const user = userEvent.setup()
      renderContent()
      await waitFor(() => screen.getByText(publicationListItem.title))
      await user.click(screen.getByText(publicationListItem.title))
      await waitFor(() => screen.getByText('Detail page'))
    })
  })
})
