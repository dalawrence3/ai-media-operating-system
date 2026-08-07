/* MSW request handlers — intercept fetch at the HTTP boundary.
   Must use absolute URLs (http://localhost) because setupServer runs in Node.js,
   where relative paths are not resolved against a document origin. */

import { http, HttpResponse } from 'msw'
import {
  WS_ID, CH_ID,
  cpWorkspace, workspaceView, healthView,
  cpChannel, cpChannel2, cpAccount1, cpAccount2,
  pipelineView, reviewItem, exceptionView, costView,
  auditView, operationView, experiment, controlEvent,
} from './fixtures'

const O = 'http://localhost:5173'
const B = `${O}/api/v1`

export const handlers = [
  // Workspaces
  http.get(`${B}/workspaces`, () =>
    HttpResponse.json([cpWorkspace]),
  ),
  http.get(`${B}/workspaces/${WS_ID}`, () =>
    HttpResponse.json(workspaceView),
  ),
  http.get(`${B}/workspaces/${WS_ID}/control-center`, () =>
    HttpResponse.json({ workspace_id: WS_ID, status: 'ok' }),
  ),
  http.get(`${B}/workspaces/${WS_ID}/health`, () =>
    HttpResponse.json(healthView),
  ),
  http.get(`${B}/workspaces/${WS_ID}/review-queue`, () =>
    HttpResponse.json([reviewItem]),
  ),
  http.get(`${B}/workspaces/${WS_ID}/exceptions`, () =>
    HttpResponse.json([exceptionView]),
  ),
  http.get(`${B}/workspaces/${WS_ID}/costs`, () =>
    HttpResponse.json(costView),
  ),
  http.get(`${B}/workspaces/${WS_ID}/audit`, () =>
    HttpResponse.json([auditView]),
  ),
  http.get(`${B}/workspaces/${WS_ID}/config`, () =>
    HttpResponse.json({}),
  ),
  http.get(`${B}/workspaces/${WS_ID}/experiments`, () =>
    HttpResponse.json([experiment]),
  ),
  http.get(`${B}/workspaces/${WS_ID}/events`, () =>
    HttpResponse.json([controlEvent]),
  ),

  // Channels
  http.get(`${B}/workspaces/${WS_ID}/channels`, () =>
    HttpResponse.json([cpChannel, cpChannel2]),
  ),
  http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}`, () =>
    HttpResponse.json({
      id: CH_ID,
      workspace_id: WS_ID,
      name: 'Channel Alpha',
      slug: 'channel-alpha',
      status: 'active',
      paused: false,
      account_count: 2,
      automation_level: 'MANUAL',
      active_pipeline_count: 1,
    }),
  ),
  http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts`, () =>
    HttpResponse.json([cpAccount1, cpAccount2]),
  ),
  http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/strategy`, () =>
    HttpResponse.json({ status: 'unavailable', message: 'No strategy assigned' }),
  ),
  http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/policy`, () =>
    HttpResponse.json({ effective_automation_level: 'MANUAL' }),
  ),

  // Pipelines
  http.get(`${B}/workspaces/${WS_ID}/pipelines`, () =>
    HttpResponse.json([pipelineView]),
  ),
  http.get(`${B}/workspaces/${WS_ID}/pipelines/${pipelineView.id}`, () =>
    HttpResponse.json(pipelineView),
  ),
  http.post(`${B}/workspaces/${WS_ID}/pipelines/${pipelineView.id}/pause`, () =>
    HttpResponse.json({ ...pipelineView, status: 'paused' }),
  ),
  http.post(`${B}/workspaces/${WS_ID}/pipelines/${pipelineView.id}/resume`, () =>
    HttpResponse.json({ ...pipelineView, status: 'running' }),
  ),
  http.post(`${B}/workspaces/${WS_ID}/pipelines/${pipelineView.id}/cancel`, () =>
    HttpResponse.json({ ...pipelineView, status: 'cancelled' }),
  ),
  http.post(`${B}/workspaces/${WS_ID}/pipelines/${pipelineView.id}/recover`, () =>
    HttpResponse.json({ ...pipelineView, status: 'running' }),
  ),

  // Reviews
  http.post(`${B}/workspaces/${WS_ID}/reviews/:itemType/:itemId/approve`, () =>
    HttpResponse.json({ ok: true }),
  ),
  http.post(`${B}/workspaces/${WS_ID}/reviews/:itemType/:itemId/reject`, () =>
    HttpResponse.json({ ok: true }),
  ),

  // Operations
  http.get(`${B}/workspaces/${WS_ID}/operations`, () =>
    HttpResponse.json([operationView]),
  ),
  http.post(`${B}/workspaces/${WS_ID}/operations/${operationView.id}/retry`, () =>
    HttpResponse.json({ ok: true }),
  ),
  http.post(`${B}/workspaces/${WS_ID}/operations/${operationView.id}/cancel`, () =>
    HttpResponse.json({ ok: true }),
  ),

  // Schedules
  http.get(`${B}/workspaces/${WS_ID}/schedules`, () =>
    HttpResponse.json([]),
  ),

  // Diagnostics
  http.get(`${B}/workspaces/${WS_ID}/diagnostics/:subject/:subjectId`, () =>
    HttpResponse.json({
      subject: 'workspace',
      subject_id: WS_ID,
      workspace_id: WS_ID,
      status: 'pass',
      findings: [],
      generated_at: '2025-01-01T12:00:00',
      contract_version: '1.0',
    }),
  ),
]
