/* API client — HTTP boundary contract tests */

import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import { api } from './client'
import { WS_ID, CH_ID, cpWorkspace, healthView, pipelineView } from '@/test/fixtures'

const B = 'http://localhost:5173/api/v1'

describe('ApiClient', () => {
  describe('workspace scope propagation', () => {
    it('includes workspace id in the URL path', async () => {
      let capturedUrl: string | undefined
      server.use(
        http.get(`${B}/workspaces/:wsId/health`, ({ request }) => {
          capturedUrl = request.url
          return HttpResponse.json(healthView)
        }),
      )
      await api.getHealth(WS_ID)
      expect(capturedUrl).toContain(`/workspaces/${WS_ID}/health`)
    })

    it('different workspace IDs produce different request paths', async () => {
      const urls: string[] = []
      server.use(
        http.get(`${B}/workspaces/:wsId/health`, ({ request }) => {
          urls.push(request.url)
          return HttpResponse.json(healthView)
        }),
      )
      await api.getHealth('ws-aaa')
      await api.getHealth('ws-bbb')
      expect(urls[0]).toContain('/ws-aaa/')
      expect(urls[1]).toContain('/ws-bbb/')
    })
  })

  describe('successful response decoding', () => {
    it('decodes workspace list', async () => {
      const result = await api.listWorkspaces()
      expect(result).toHaveLength(1)
      expect(result[0].id).toBe(WS_ID)
      expect(result[0].name).toBe('Test Workspace')
    })

    it('decodes workspace summary', async () => {
      const result = await api.getWorkspaceSummary(WS_ID)
      expect(result.id).toBe(WS_ID)
      expect(result.channel_count).toBe(2)
    })

    it('decodes health view', async () => {
      const result = await api.getHealth(WS_ID)
      expect(result.overall_status).toBe('healthy')
      expect(result.event_bus_ok).toBe(true)
    })

    it('decodes pipeline list', async () => {
      const result = await api.listPipelines(WS_ID)
      expect(result).toHaveLength(1)
      expect(result[0].id).toBe(pipelineView.id)
      expect(result[0].current_stage).toBe('script_generation')
    })

    it('decodes channel list', async () => {
      const result = await api.listChannels(WS_ID)
      expect(result).toHaveLength(2)
      expect(result[0].id).toBe(CH_ID)
    })

    it('decodes channel accounts', async () => {
      const result = await api.listChannelAccounts(WS_ID, CH_ID)
      expect(result).toHaveLength(2)
    })
  })

  describe('DEV actor header', () => {
    it('sends X-Dev-Actor header on every request', async () => {
      let capturedHeader: string | null = null
      server.use(
        http.get(`${B}/workspaces`, ({ request }) => {
          capturedHeader = request.headers.get('X-Dev-Actor')
          return HttpResponse.json([cpWorkspace])
        }),
      )
      await api.listWorkspaces()
      expect(capturedHeader).toBe('dev:studio-user')
    })
  })

  describe('backend error mapping', () => {
    it('throws with HTTP status and detail on 4xx', async () => {
      server.use(
        http.get(`${B}/workspaces/:wsId/health`, () =>
          HttpResponse.json({ detail: 'Workspace not found' }, { status: 404 }),
        ),
      )
      await expect(api.getHealth('no-such-ws')).rejects.toMatchObject({
        status: 404,
        message: 'Workspace not found',
      })
    })

    it('throws with HTTP status on 500 when body is not JSON', async () => {
      server.use(
        http.get(`${B}/workspaces/:wsId/health`, () =>
          new HttpResponse('Internal Server Error', {
            status: 500,
            headers: { 'Content-Type': 'text/plain' },
          }),
        ),
      )
      await expect(api.getHealth(WS_ID)).rejects.toMatchObject({
        status: 500,
      })
    })

    it('throws authorization error on 403', async () => {
      server.use(
        http.post(`${B}/workspaces/:wsId/reviews/:type/:id/approve`, () =>
          HttpResponse.json({ detail: 'Actor not authorized' }, { status: 403 }),
        ),
      )
      await expect(
        api.approveReviewItem(WS_ID, 'script', 'item-001'),
      ).rejects.toMatchObject({ status: 403, message: 'Actor not authorized' })
    })
  })

  describe('query parameter propagation', () => {
    it('passes status filter to pipelines endpoint', async () => {
      let capturedUrl: string | undefined
      server.use(
        http.get(`${B}/workspaces/:wsId/pipelines`, ({ request }) => {
          capturedUrl = request.url
          return HttpResponse.json([pipelineView])
        }),
      )
      await api.listPipelines(WS_ID, { status: 'running' })
      expect(capturedUrl).toContain('status=running')
    })

    it('passes period to costs endpoint', async () => {
      let capturedUrl: string | undefined
      server.use(
        http.get(`${B}/workspaces/:wsId/costs`, ({ request }) => {
          capturedUrl = request.url
          return HttpResponse.json({
            workspace_id: WS_ID, period: 'weekly', total_usd: 0,
            channel_breakdown: [], budget_policies: [],
            warning_active: false, block_active: false,
          })
        }),
      )
      await api.getCosts(WS_ID, 'weekly')
      expect(capturedUrl).toContain('period=weekly')
    })

    it('omits undefined params from query string', async () => {
      let capturedUrl: string | undefined
      server.use(
        http.get(`${B}/workspaces/:wsId/pipelines`, ({ request }) => {
          capturedUrl = request.url
          return HttpResponse.json([])
        }),
      )
      await api.listPipelines(WS_ID)
      expect(capturedUrl).not.toContain('undefined')
      expect(capturedUrl).not.toContain('null')
    })
  })
})
