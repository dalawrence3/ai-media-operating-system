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
  topicView, topicView2, stageArtifactResolved, stageDiagnosticReport,
  publicationListItem, publicationDetail, publicationAnalytics, publicationAnalyticsHistory,
  TOPIC_ID, PIPE_ID, PUB_ID,
  publishingAuthorizationAuthorized,
  publishingAuthorizationUnauthorized,
  publicationVisualQuality,
} from './fixtures'

export const MOCK_NEW_CHANNEL_ID = 'ch-new-001'
export const MOCK_NEW_PIPELINE_ID = 'pipe-new-001'
export const MOCK_NEW_SCHEDULE_ID = 'sched-new-001'
export const MOCK_NEW_ACCOUNT_ID = 'acct-new-001'

const O = 'http://localhost:5173'
const B = `${O}/api/v1`

export const VALID_EMAIL = 'alice@example.com'
export const VALID_PASSWORD = 'hunter2-long-enough'
export const MOCK_ACCESS_TOKEN = 'mock-access-token-abcdef'
export const MOCK_REFRESH_TOKEN = 'mock-refresh-token-xyz'
export const MOCK_NEW_ACCESS_TOKEN = 'mock-refreshed-access-token-new'

export const handlers = [
  // ── Auth endpoints ──────────────────────────────────────────────────────
  http.post(`${O}/api/v1/auth/login`, async ({ request }) => {
    const body = await request.json() as { email?: string; password?: string }
    if (body.email === VALID_EMAIL && body.password === VALID_PASSWORD) {
      return HttpResponse.json({
        access_token: MOCK_ACCESS_TOKEN,
        refresh_token: MOCK_REFRESH_TOKEN,
        token_type: 'bearer',
      })
    }
    return HttpResponse.json(
      { detail: 'Invalid email or password' },
      { status: 401 },
    )
  }),

  http.post(`${O}/api/v1/auth/refresh`, async ({ request }) => {
    const body = await request.json() as { refresh_token?: string }
    if (body.refresh_token === MOCK_REFRESH_TOKEN) {
      return HttpResponse.json({
        access_token: MOCK_NEW_ACCESS_TOKEN,
        token_type: 'bearer',
      })
    }
    return HttpResponse.json(
      { detail: 'Refresh token not found' },
      { status: 401 },
    )
  }),

  http.post(`${O}/api/v1/auth/logout`, () =>
    HttpResponse.json({ revoked: true }),
  ),

  http.get(`${O}/api/v1/auth/me`, ({ request }) => {
    const auth = request.headers.get('Authorization')
    if (auth === `Bearer ${MOCK_ACCESS_TOKEN}` || auth === `Bearer ${MOCK_NEW_ACCESS_TOKEN}`) {
      return HttpResponse.json({
        user_id: 1,
        email: VALID_EMAIL,
        actor: 'user:1',
        workspace_roles: { [WS_ID]: 'operator' },
      })
    }
    return HttpResponse.json({ detail: 'Authentication required' }, { status: 401 })
  }),


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
  http.post(`${B}/workspaces/${WS_ID}/channels`, async ({ request }) => {
    const body = await request.json() as { name: string; slug: string; description?: string }
    return HttpResponse.json({
      ...cpChannel,
      id: MOCK_NEW_CHANNEL_ID,
      name: body.name,
      slug: body.slug,
      description: body.description ?? null,
    }, { status: 201 })
  }),
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
  http.post(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts`, async ({ request }) => {
    const body = await request.json() as {
      platform_id: string; external_account_id: string; display_name: string
    }
    return HttpResponse.json({
      id: MOCK_NEW_ACCOUNT_ID,
      channel_id: CH_ID,
      platform_id: body.platform_id,
      platform_key: body.platform_id,
      external_account_id: body.external_account_id,
      display_name: body.display_name,
      status: 'disconnected',
      credential_profile_id: null,
      actor: 'dev:studio-user',
      created_at: '2025-01-01T00:00:00',
      updated_at: '2025-01-01T00:00:00',
      metadata_json: null,
    })
  }),
  // OAuth connection status — returns disconnected by default (no live OAuth in tests)
  http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts/:accountId/connection`, () =>
    HttpResponse.json({
      account_id: 'mock',
      connected: false,
      provider_channel_id: null,
      channel_title: null,
      verified_at: null,
      granted_scopes: [],
      upload_scope_granted: false,
      analytics_scope_granted: false,
      release_scope_granted: false,
      credential_status: null,
      health_status: null,
    }),
  ),
  http.post(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts/:accountId/oauth/youtube/start`, () =>
    HttpResponse.json({ authorization_url: 'https://accounts.google.com/o/oauth2/auth?state=test' }),
  ),
  http.delete(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts/:accountId/oauth/youtube`, () =>
    HttpResponse.json({ status: 'disconnected', account_id: 'mock' }),
  ),
  http.post(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts/:accountId/oauth/youtube/upgrade-analytics`, () =>
    HttpResponse.json({ authorization_url: 'https://accounts.google.com/o/oauth2/auth?state=analytics-test' }),
  ),
  http.post(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/accounts/:accountId/oauth/youtube/upgrade-release`, () =>
    HttpResponse.json({ authorization_url: 'https://accounts.google.com/o/oauth2/auth?state=release-test' }),
  ),
  http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/strategy`, () =>
    HttpResponse.json({ status: 'unavailable', message: 'No strategy assigned', profile: null, effective: null }),
  ),
  http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/strategy/history`, () =>
    HttpResponse.json([]),
  ),
  http.post(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/strategy`, async ({ request }) => {
    const body = await request.json() as { config: Record<string, unknown> }
    return HttpResponse.json({
      id: 'strat-new-001',
      channel_id: CH_ID,
      version: 2,
      config_json: JSON.stringify(body.config),
      actor: 'dev:studio-user',
      created_at: '2026-08-28T01:00:00',
      is_active: true,
    })
  }),
  http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/policy`, () =>
    HttpResponse.json({ effective_automation_level: 'MANUAL' }),
  ),
  http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/readiness`, () =>
    HttpResponse.json({
      channel_id: CH_ID,
      checks: [
        { key: 'market_intelligence_configured', label: 'Market intelligence configured', ready: false, detail: 'No YouTube Data API key configured' },
      ],
      ready_for_decision_automation: false,
      authorized_for_public_publishing: false,
    }),
  ),
  http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/automation-policy`, () =>
    HttpResponse.json({ policy: null, active_slots: [] }),
  ),
  http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/publishing-authorization`, () =>
    HttpResponse.json(publishingAuthorizationUnauthorized),
  ),
  http.put(
    `${B}/workspaces/${WS_ID}/channels/${CH_ID}/publishing-authorization`,
    async ({ request }) => {
      const body = await request.json() as Record<string, unknown>
      if (body.authorized === true && body.confirm !== true) {
        return HttpResponse.json(
          { detail: 'Granting public-publishing authorization requires an explicit "confirm": true.' },
          { status: 422 },
        )
      }
      return HttpResponse.json({
        ...publishingAuthorizationAuthorized.authorization,
        authorized: body.authorized ?? false,
        max_publications_per_24h: body.max_publications_per_24h ?? 1,
      })
    },
  ),
  http.put(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/automation-policy`, async ({ request }) => {
    const body = await request.json() as Record<string, unknown>
    return HttpResponse.json({
      channel_id: CH_ID,
      workspace_id: WS_ID,
      decision_automation_enabled: body.decision_automation_enabled ?? false,
      production_automation_enabled: body.production_automation_enabled ?? false,
      cadence_type: body.cadence_type ?? 'daily',
      cadence_interval_days: body.cadence_interval_days ?? null,
      cadence_cron: null,
      preferred_local_hour: body.preferred_local_hour ?? 9,
      timezone: body.timezone ?? null,
      queue_target: body.queue_target ?? 1,
      market_refresh_max_age_hours: 12,
      semantic_fit_max_evaluations_per_run: 5,
      last_decision_at: null,
      last_decision_outcome: null,
      actor: 'dev:studio-user',
      created_at: '2026-08-29T00:00:00',
      updated_at: '2026-08-29T00:00:00',
    })
  }),

  // Pipelines
  http.post(`${B}/workspaces/${WS_ID}/pipelines`, async ({ request }) => {
    const body = await request.json() as { channel_id: string; idempotency_key: string }
    return HttpResponse.json({
      ...pipelineView,
      id: MOCK_NEW_PIPELINE_ID,
      channel_id: body.channel_id,
      idempotency_key: body.idempotency_key,
      status: 'pending',
    })
  }),
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
  http.post(`${B}/workspaces/${WS_ID}/schedules/:scheduleId/pause`, ({ params }) =>
    HttpResponse.json({
      id: params.scheduleId,
      workspace_id: WS_ID,
      channel_id: null,
      name: 'Daily publish',
      operation_type: 'start_pipeline',
      schedule_type: 'cron',
      schedule_config: {},
      timezone: 'UTC',
      is_active: false,
      last_run_at: null,
      next_run_at: null,
      actor: 'dev:studio-user',
      created_at: '2025-01-01T00:00:00',
      updated_at: '2025-01-01T00:00:00',
    }),
  ),
  http.post(`${B}/workspaces/${WS_ID}/schedules/:scheduleId/resume`, ({ params }) =>
    HttpResponse.json({
      id: params.scheduleId,
      workspace_id: WS_ID,
      channel_id: null,
      name: 'Daily publish',
      operation_type: 'start_pipeline',
      schedule_type: 'cron',
      schedule_config: {},
      timezone: 'UTC',
      is_active: true,
      last_run_at: null,
      next_run_at: null,
      actor: 'dev:studio-user',
      created_at: '2025-01-01T00:00:00',
      updated_at: '2025-01-01T00:00:00',
    }),
  ),
  http.delete(`${B}/workspaces/${WS_ID}/schedules/:scheduleId`, () =>
    HttpResponse.json({ deleted: true }),
  ),
  http.post(`${B}/workspaces/${WS_ID}/schedules`, async ({ request }) => {
    const body = await request.json() as { name: string; operation_type: string; schedule_type: string; schedule_config: Record<string, unknown> }
    return HttpResponse.json({
      id: MOCK_NEW_SCHEDULE_ID,
      workspace_id: WS_ID,
      channel_id: null,
      name: body.name,
      operation_type: body.operation_type,
      schedule_type: body.schedule_type,
      schedule_config: body.schedule_config,
      timezone: 'UTC',
      is_active: true,
      last_run_at: null,
      next_run_at: null,
      actor: 'dev:studio-user',
      created_at: '2025-01-01T00:00:00',
      updated_at: '2025-01-01T00:00:00',
    })
  }),
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

  // Topics
  http.get(`${B}/workspaces/${WS_ID}/topics`, () =>
    HttpResponse.json([topicView, topicView2]),
  ),
  http.post(`${B}/workspaces/${WS_ID}/topics`, async ({ request }) => {
    const body = await request.json() as { title: string; angle?: string }
    return HttpResponse.json({
      ...topicView,
      id: TOPIC_ID + 100,
      title: body.title,
      angle: body.angle ?? '',
    }, { status: 201 })
  }),

  // Pipeline artifact + pipeline-scoped diagnostics + advance
  http.get(`${B}/workspaces/${WS_ID}/pipelines/${PIPE_ID}/stages/:stage/artifact`, ({ params }) => {
    if (params.stage === 'research') return HttpResponse.json(stageArtifactResolved)
    return HttpResponse.json({
      pipeline_id: PIPE_ID, stage: params.stage, workspace_id: WS_ID,
      artifact_id: null, artifact_type: null, stage_status: 'not_started',
      attempt_number: 0, started_at: null, completed_at: null, duration_ms: null, error_message: null,
      resolved: false, reason: 'no_artifact',
    })
  }),
  http.get(`${B}/workspaces/${WS_ID}/pipelines/${PIPE_ID}/diagnostics/:stage`, () =>
    HttpResponse.json(stageDiagnosticReport),
  ),
  http.post(`${B}/workspaces/${WS_ID}/pipelines/${PIPE_ID}/advance`, async ({ request }) => {
    const body = await request.json() as { stage: string }
    return HttpResponse.json({ ...pipelineView, current_stage: body.stage, status: 'running' })
  }),

  // Publications
  http.get(`${B}/workspaces/${WS_ID}/publications`, () =>
    HttpResponse.json([publicationListItem]),
  ),
  http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}`, () =>
    HttpResponse.json(publicationDetail),
  ),
  http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/stream`, () =>
    new HttpResponse(new Uint8Array([0, 1, 2, 3]).buffer, {
      headers: { 'Content-Type': 'video/mp4' },
    }),
  ),
  http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/visual-quality`, () =>
    HttpResponse.json(publicationVisualQuality),
  ),

  http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/analytics`, () =>
    HttpResponse.json(publicationAnalytics),
  ),
  http.get(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/analytics/history`, () =>
    HttpResponse.json(publicationAnalyticsHistory),
  ),
  http.post(`${B}/workspaces/${WS_ID}/publications/${PUB_ID}/release-public`, () =>
    HttpResponse.json({ visibility: 'public', reconciled: false }),
  ),

  // Analytics (default: empty — no data seeded yet)
  http.get(`${B}/workspaces/${WS_ID}/analytics/aggregates`, () =>
    HttpResponse.json([]),
  ),
  http.get(`${B}/workspaces/${WS_ID}/analytics/snapshots`, () =>
    HttpResponse.json([]),
  ),

  // Market intelligence + cross-publication learning (default: empty — no data seeded yet)
  http.get(`${B}/workspaces/${WS_ID}/market/opportunities`, () =>
    HttpResponse.json([]),
  ),
  http.get(`${B}/workspaces/${WS_ID}/market/experiments`, () =>
    HttpResponse.json([]),
  ),
  http.get(`${B}/workspaces/${WS_ID}/market/strategy-briefs`, () =>
    HttpResponse.json([]),
  ),
  http.get(`${B}/workspaces/${WS_ID}/channels/${CH_ID}/cross-publication`, () =>
    HttpResponse.json({ channel_id: CH_ID, baselines: [], feature_observations: [] }),
  ),
  http.get(`${B}/workspaces/${WS_ID}/market/opportunities/:opportunityId/evidence`, () =>
    HttpResponse.json({ opportunity_id: 1, evidence_count: 0, snapshots: [] }),
  ),

  // Learning (default: empty — no data seeded yet)
  http.get(`${B}/workspaces/${WS_ID}/recommendations`, () =>
    HttpResponse.json([]),
  ),
  http.post(`${B}/workspaces/${WS_ID}/recommendations/:id/accept`, () =>
    HttpResponse.json({ id: 1, recommendation_id: 1, topic_id: 1, event_type: 'accepted',
      reviewer: 'dev:studio-user', notes: '', expected_outcome: '', input_hash: 'h', created_at: '2025-01-01T00:00:00' }),
  ),
  http.post(`${B}/workspaces/${WS_ID}/recommendations/:id/reject`, () =>
    HttpResponse.json({ id: 2, recommendation_id: 1, topic_id: 1, event_type: 'rejected',
      reviewer: 'dev:studio-user', notes: 'not relevant', expected_outcome: '', input_hash: 'h2', created_at: '2025-01-01T00:00:00' }),
  ),
]
