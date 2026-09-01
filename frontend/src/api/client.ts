/* Centralized typed API client.
   ALL backend interaction goes through this module.
   Do not scatter fetch() calls through page components.

   Auth flow (production):
     - Attaches Authorization: Bearer <access_token> on every request.
     - On 401: attempts one silent token refresh, then retries the original request.
     - On failed refresh: calls the registered onAuthLost callback so the app
       can clear session state and redirect to login.
     - On 403: throws a ForbiddenError (typed, not generic).
     - Tokens are never logged or stored here — the AuthContext owns storage.

   Dev mode (Vite DEV build only, when no access token is present):
     - Sends X-Dev-Actor header as a convenience for local iteration without JWT.
     - This header is silently ignored by the backend in production mode.
*/

import type {
  AccountView,
  AnalyticsAggregate,
  AnalyticsSnapshot,
  AuditView,
  AutonomyPolicy,
  AutonomyReadinessResponse,
  ChannelAutomationPolicyResponse,
  ChannelPublishingAuthorization,
  ChannelPublishingAuthorizationResponse,
  UpdatePublishingAuthorizationRequest,
  ChannelView,
  CostView,
  CPAccount,
  CPChannel,
  CPWorkspace,
  ControlEvent,
  CrossPublicationLearning,
  DiagnosticReport,
  Experiment,
  ExperimentStrategyBrief,
  ExceptionView,
  HealthView,
  MarketExperiment,
  MarketOpportunity,
  OperationView,
  OpportunityEvidenceResponse,
  OptimizationRecommendation,
  PipelineView,
  PublicationAnalytics,
  PublicationVisualQuality,
  PublicationAnalyticsHistoryEntry,
  PublicationDetail,
  PublicationListItem,
  RecommendationReviewEvent,
  ReviewItemView,
  ScheduleView,
  StageArtifact,
  ChannelStrategyResponse,
  StrategyConfig,
  StrategyProfile,
  TopicView,
  UpdateAutomationPolicyRequest,
  WorkspaceView,
  YouTubeVerificationResult,
} from './types'

const BASE_URL = '/api/v1'

// ── Typed errors ────────────────────────────────────────────────────────────

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export class ForbiddenError extends ApiError {
  constructor(message = 'Forbidden') {
    super(403, message)
    this.name = 'ForbiddenError'
  }
}

export class UnauthorizedError extends ApiError {
  constructor(message = 'Unauthorized') {
    super(401, message)
    this.name = 'UnauthorizedError'
  }
}

// ── Auth callback interface ─────────────────────────────────────────────────

interface AuthCallbacks {
  getToken: () => string | null
  onRefreshNeeded: () => Promise<string | null>
  onAuthLost: () => void
}

// ── Client ───────────────────────────────────────────────────────────────────

class ApiClient {
  private _auth: AuthCallbacks | null = null

  /**
   * Wire in auth callbacks from the AuthProvider.
   * Must be called before any authenticated requests are made.
   */
  setAuthCallbacks(callbacks: AuthCallbacks): void {
    this._auth = callbacks
  }

  clearAuthCallbacks(): void {
    this._auth = null
  }

  private _buildHeaders(token: string | null): HeadersInit {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    }

    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    } else if (import.meta.env.DEV) {
      // Dev convenience: when no token is present in a local Vite dev build,
      // send X-Dev-Actor so the backend's dev-auth fallback activates.
      // This header is ignored by the backend in production mode.
      headers['X-Dev-Actor'] = 'dev:studio-user'
    }

    return headers
  }

  private async _doRequest<T>(
    method: string,
    path: string,
    token: string | null,
    body?: unknown,
    params?: Record<string, string | number | boolean | null | undefined>,
  ): Promise<T> {
    const url = new URL(BASE_URL + path, window.location.origin)
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        if (v !== null && v !== undefined) {
          url.searchParams.set(k, String(v))
        }
      }
    }

    const res = await fetch(url.toString(), {
      method,
      headers: this._buildHeaders(token),
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(30_000),
    })

    if (res.status === 403) {
      let detail = 'Forbidden'
      try {
        const err = await res.json()
        detail = err.detail ?? detail
      } catch { /* ignore */ }
      throw new ForbiddenError(detail)
    }

    if (!res.ok) {
      let detail = `HTTP ${res.status}`
      try {
        const err = await res.json()
        detail = err.detail ?? detail
      } catch { /* ignore */ }
      throw new ApiError(res.status, detail)
    }

    return res.json() as Promise<T>
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
    params?: Record<string, string | number | boolean | null | undefined>,
  ): Promise<T> {
    const token = this._auth?.getToken() ?? null

    try {
      return await this._doRequest<T>(method, path, token, body, params)
    } catch (err) {
      // On 401: attempt one silent refresh, then retry once.
      if (
        err instanceof ApiError &&
        err.status === 401 &&
        this._auth
      ) {
        const newToken = await this._auth.onRefreshNeeded()
        if (newToken) {
          // Retry with the fresh token.
          return await this._doRequest<T>(method, path, newToken, body, params)
        }
        // Refresh failed — session is lost.
        this._auth.onAuthLost()
        throw new UnauthorizedError('Session expired. Please log in again.')
      }
      throw err
    }
  }

  // ── Workspaces ──────────────────────────────────────────────────────────

  listWorkspaces(status?: string) {
    return this.request<CPWorkspace[]>('GET', '/workspaces', undefined, { status })
  }

  getWorkspaceSummary(workspaceId: string) {
    return this.request<WorkspaceView>('GET', `/workspaces/${workspaceId}`)
  }

  getControlCenter(workspaceId: string) {
    return this.request<Record<string, unknown>>('GET', `/workspaces/${workspaceId}/control-center`)
  }

  getHealth(workspaceId: string) {
    return this.request<HealthView>('GET', `/workspaces/${workspaceId}/health`)
  }

  getReviewQueue(workspaceId: string) {
    return this.request<ReviewItemView[]>('GET', `/workspaces/${workspaceId}/review-queue`)
  }

  getExceptionQueue(workspaceId: string) {
    return this.request<ExceptionView[]>('GET', `/workspaces/${workspaceId}/exceptions`)
  }

  getCosts(workspaceId: string, period?: string) {
    return this.request<CostView>('GET', `/workspaces/${workspaceId}/costs`, undefined, { period })
  }

  getAuditTimeline(workspaceId: string, limit?: number) {
    return this.request<AuditView[]>('GET', `/workspaces/${workspaceId}/audit`, undefined, { limit })
  }

  getEffectiveConfig(workspaceId: string, channelId?: string, accountId?: string) {
    return this.request<Record<string, unknown>>(
      'GET',
      `/workspaces/${workspaceId}/config`,
      undefined,
      { channel_id: channelId, platform_account_id: accountId },
    )
  }

  listExperiments(workspaceId: string) {
    return this.request<Experiment[]>('GET', `/workspaces/${workspaceId}/experiments`)
  }

  listEvents(workspaceId: string, eventType?: string, limit?: number) {
    return this.request<ControlEvent[]>(
      'GET',
      `/workspaces/${workspaceId}/events`,
      undefined,
      { event_type: eventType, limit },
    )
  }

  // ── Channels ─────────────────────────────────────────────────────────────

  listChannels(workspaceId: string) {
    return this.request<CPChannel[]>('GET', `/workspaces/${workspaceId}/channels`)
  }

  createChannel(workspaceId: string, body: { name: string; slug: string; description?: string }) {
    return this.request<CPChannel>('POST', `/workspaces/${workspaceId}/channels`, body)
  }

  getChannelSummary(workspaceId: string, channelId: string) {
    return this.request<ChannelView>('GET', `/workspaces/${workspaceId}/channels/${channelId}`)
  }

  listChannelAccounts(workspaceId: string, channelId: string) {
    return this.request<CPAccount[]>(
      'GET',
      `/workspaces/${workspaceId}/channels/${channelId}/accounts`,
    )
  }

  createPlatformAccount(
    workspaceId: string,
    channelId: string,
    body: { platform_id: string; external_account_id: string; display_name: string },
  ) {
    return this.request<CPAccount>(
      'POST',
      `/workspaces/${workspaceId}/channels/${channelId}/accounts`,
      body,
    )
  }

  getChannelStrategy(workspaceId: string, channelId: string) {
    return this.request<ChannelStrategyResponse>(
      'GET',
      `/workspaces/${workspaceId}/channels/${channelId}/strategy`,
    )
  }

  getChannelReadiness(workspaceId: string, channelId: string) {
    return this.request<AutonomyReadinessResponse>(
      'GET',
      `/workspaces/${workspaceId}/channels/${channelId}/readiness`,
    )
  }

  getChannelAutomationPolicy(workspaceId: string, channelId: string) {
    return this.request<ChannelAutomationPolicyResponse>(
      'GET',
      `/workspaces/${workspaceId}/channels/${channelId}/automation-policy`,
    )
  }

  updateChannelAutomationPolicy(workspaceId: string, channelId: string, body: UpdateAutomationPolicyRequest) {
    return this.request<AutonomyPolicy>(
      'PUT',
      `/workspaces/${workspaceId}/channels/${channelId}/automation-policy`,
      body,
    )
  }

  getChannelPublishingAuthorization(workspaceId: string, channelId: string) {
    return this.request<ChannelPublishingAuthorizationResponse>(
      'GET',
      `/workspaces/${workspaceId}/channels/${channelId}/publishing-authorization`,
    )
  }

  updateChannelPublishingAuthorization(
    workspaceId: string,
    channelId: string,
    body: UpdatePublishingAuthorizationRequest,
  ) {
    return this.request<ChannelPublishingAuthorization>(
      'PUT',
      `/workspaces/${workspaceId}/channels/${channelId}/publishing-authorization`,
      body,
    )
  }

  listChannelStrategyHistory(workspaceId: string, channelId: string) {
    return this.request<StrategyProfile[]>(
      'GET',
      `/workspaces/${workspaceId}/channels/${channelId}/strategy/history`,
    )
  }

  createChannelStrategyVersion(workspaceId: string, channelId: string, config: StrategyConfig) {
    return this.request<StrategyProfile>(
      'POST',
      `/workspaces/${workspaceId}/channels/${channelId}/strategy`,
      { config },
    )
  }

  getChannelPolicy(workspaceId: string, channelId: string) {
    return this.request<{ effective_automation_level: string }>(
      'GET',
      `/workspaces/${workspaceId}/channels/${channelId}/policy`,
    )
  }

  // ── OAuth ─────────────────────────────────────────────────────────────────

  startYouTubeOAuth(workspaceId: string, channelId: string, accountId: string) {
    return this.request<{ authorization_url: string }>(
      'POST',
      `/workspaces/${workspaceId}/channels/${channelId}/accounts/${accountId}/oauth/youtube/start`,
    )
  }

  disconnectYouTubeAccount(workspaceId: string, channelId: string, accountId: string) {
    return this.request<{ status: string; account_id: string }>(
      'DELETE',
      `/workspaces/${workspaceId}/channels/${channelId}/accounts/${accountId}/oauth/youtube`,
    )
  }

  getAccountConnectionStatus(workspaceId: string, channelId: string, accountId: string) {
    return this.request<{
      account_id: string
      connected: boolean
      provider_channel_id: string | null
      channel_title: string | null
      verified_at: string | null
      granted_scopes: string[]
      upload_scope_granted: boolean
      analytics_scope_granted: boolean
      release_scope_granted: boolean
      credential_status: string | null
      health_status: string | null
    }>(
      'GET',
      `/workspaces/${workspaceId}/channels/${channelId}/accounts/${accountId}/connection`,
    )
  }

  upgradeYouTubeUploadScope(workspaceId: string, channelId: string, accountId: string) {
    return this.request<{ authorization_url: string }>(
      'POST',
      `/workspaces/${workspaceId}/channels/${channelId}/accounts/${accountId}/oauth/youtube/upgrade-upload`,
    )
  }

  upgradeYouTubeAnalyticsScope(workspaceId: string, channelId: string, accountId: string) {
    return this.request<{ authorization_url: string }>(
      'POST',
      `/workspaces/${workspaceId}/channels/${channelId}/accounts/${accountId}/oauth/youtube/upgrade-analytics`,
    )
  }

  /** Request youtube.force-ssl, the scope required to change a video's privacy
      status. The backend bundles the already-granted scopes into the same
      request so upload and analytics survive the re-consent. */
  upgradeYouTubeReleaseScope(workspaceId: string, channelId: string, accountId: string) {
    return this.request<{ authorization_url: string }>(
      'POST',
      `/workspaces/${workspaceId}/channels/${channelId}/accounts/${accountId}/oauth/youtube/upgrade-release`,
    )
  }

  verifyYouTubeConnection(workspaceId: string, channelId: string, accountId: string) {
    return this.request<YouTubeVerificationResult>(
      'POST',
      `/workspaces/${workspaceId}/channels/${channelId}/accounts/${accountId}/oauth/youtube/verify`,
    )
  }

  // ── Accounts ─────────────────────────────────────────────────────────────

  getAccountSummary(workspaceId: string, accountId: string) {
    return this.request<AccountView>('GET', `/workspaces/${workspaceId}/accounts/${accountId}`)
  }

  // ── Pipelines ────────────────────────────────────────────────────────────

  listPipelines(
    workspaceId: string,
    opts?: { status?: string; channelId?: string; limit?: number },
  ) {
    return this.request<PipelineView[]>('GET', `/workspaces/${workspaceId}/pipelines`, undefined, {
      status: opts?.status,
      channel_id: opts?.channelId,
      limit: opts?.limit,
    })
  }

  getPipeline(workspaceId: string, pipelineId: string) {
    return this.request<PipelineView>(
      'GET',
      `/workspaces/${workspaceId}/pipelines/${pipelineId}`,
    )
  }

  pausePipeline(workspaceId: string, pipelineId: string) {
    return this.request<PipelineView>(
      'POST',
      `/workspaces/${workspaceId}/pipelines/${pipelineId}/pause`,
    )
  }

  resumePipeline(workspaceId: string, pipelineId: string) {
    return this.request<PipelineView>(
      'POST',
      `/workspaces/${workspaceId}/pipelines/${pipelineId}/resume`,
    )
  }

  cancelPipeline(workspaceId: string, pipelineId: string) {
    return this.request<PipelineView>(
      'POST',
      `/workspaces/${workspaceId}/pipelines/${pipelineId}/cancel`,
    )
  }

  recoverPipeline(workspaceId: string, pipelineId: string) {
    return this.request<PipelineView>(
      'POST',
      `/workspaces/${workspaceId}/pipelines/${pipelineId}/recover`,
    )
  }

  executePipelineStage(workspaceId: string, pipelineId: string, stage: string) {
    return this.request<PipelineView>(
      'POST',
      `/workspaces/${workspaceId}/pipelines/${pipelineId}/execute-stage`,
      { stage },
    )
  }

  startPipeline(workspaceId: string, body: {
    channel_id: string
    idempotency_key: string
    platform_account_id?: string
    topic_id?: number | null
    end_stage?: string
  }) {
    return this.request<PipelineView>('POST', `/workspaces/${workspaceId}/pipelines`, body)
  }

  advancePipelineStage(
    workspaceId: string,
    pipelineId: string,
    body: { stage: string; artifact_id?: string; artifact_type?: string },
  ) {
    return this.request<PipelineView>(
      'POST',
      `/workspaces/${workspaceId}/pipelines/${pipelineId}/advance`,
      body,
    )
  }

  getStageArtifact(workspaceId: string, pipelineId: string, stage: string) {
    return this.request<StageArtifact>(
      'GET',
      `/workspaces/${workspaceId}/pipelines/${pipelineId}/stages/${stage}/artifact`,
    )
  }

  getPipelineDiagnostics(workspaceId: string, pipelineId: string, stage: string) {
    return this.request<DiagnosticReport>(
      'GET',
      `/workspaces/${workspaceId}/pipelines/${pipelineId}/diagnostics/${stage}`,
    )
  }

  // ── Reviews ──────────────────────────────────────────────────────────────

  approveReviewItem(
    workspaceId: string,
    itemType: string,
    itemId: string,
    notes?: string,
  ) {
    return this.request<Record<string, unknown>>(
      'POST',
      `/workspaces/${workspaceId}/reviews/${itemType}/${itemId}/approve`,
      { notes: notes ?? '' },
    )
  }

  rejectReviewItem(
    workspaceId: string,
    itemType: string,
    itemId: string,
    reason: string,
    notes?: string,
  ) {
    return this.request<Record<string, unknown>>(
      'POST',
      `/workspaces/${workspaceId}/reviews/${itemType}/${itemId}/reject`,
      { reason, notes },
    )
  }

  // ── Operations ───────────────────────────────────────────────────────────

  listOperations(
    workspaceId: string,
    opts?: { status?: string; channelId?: string; limit?: number },
  ) {
    return this.request<OperationView[]>(
      'GET',
      `/workspaces/${workspaceId}/operations`,
      undefined,
      {
        status: opts?.status,
        channel_id: opts?.channelId,
        limit: opts?.limit,
      },
    )
  }

  retryOperation(workspaceId: string, operationId: string) {
    return this.request<Record<string, unknown>>(
      'POST',
      `/workspaces/${workspaceId}/operations/${operationId}/retry`,
    )
  }

  cancelOperation(workspaceId: string, operationId: string) {
    return this.request<Record<string, unknown>>(
      'POST',
      `/workspaces/${workspaceId}/operations/${operationId}/cancel`,
    )
  }

  // ── Schedules ────────────────────────────────────────────────────────────

  listSchedules(workspaceId: string, isActive?: boolean) {
    return this.request<ScheduleView[]>(
      'GET',
      `/workspaces/${workspaceId}/schedules`,
      undefined,
      { is_active: isActive },
    )
  }

  createSchedule(workspaceId: string, body: {
    name: string
    operation_type: string
    schedule_type: string
    schedule_config: Record<string, unknown>
    channel_id?: string
    timezone?: string
  }) {
    return this.request<ScheduleView>('POST', `/workspaces/${workspaceId}/schedules`, body)
  }

  pauseSchedule(workspaceId: string, scheduleId: string) {
    return this.request<ScheduleView>(
      'POST',
      `/workspaces/${workspaceId}/schedules/${scheduleId}/pause`,
    )
  }

  resumeSchedule(workspaceId: string, scheduleId: string) {
    return this.request<ScheduleView>(
      'POST',
      `/workspaces/${workspaceId}/schedules/${scheduleId}/resume`,
    )
  }

  deleteSchedule(workspaceId: string, scheduleId: string) {
    return this.request<{ deleted: boolean }>(
      'DELETE',
      `/workspaces/${workspaceId}/schedules/${scheduleId}`,
    )
  }

  // ── Topics ───────────────────────────────────────────────────────────────

  listTopics(workspaceId: string) {
    return this.request<TopicView[]>('GET', `/workspaces/${workspaceId}/topics`)
  }

  createTopic(workspaceId: string, body: { title: string; angle?: string }) {
    return this.request<TopicView>('POST', `/workspaces/${workspaceId}/topics`, body)
  }

  // ── Diagnostics ──────────────────────────────────────────────────────────

  getDiagnostics(workspaceId: string, subject: string, subjectId: string) {
    return this.request<DiagnosticReport>(
      'GET',
      `/workspaces/${workspaceId}/diagnostics/${subject}/${subjectId}`,
    )
  }

  // ── Analytics ────────────────────────────────────────────────────────────

  listAnalyticsAggregates(
    workspaceId: string,
    metricName?: string,
    periodType?: string,
    publicationId?: number,
  ) {
    return this.request<AnalyticsAggregate[]>(
      'GET',
      `/workspaces/${workspaceId}/analytics/aggregates`,
      undefined,
      { metric_name: metricName, period_type: periodType, publication_id: publicationId },
    )
  }

  listAnalyticsSnapshots(workspaceId: string, limit?: number) {
    return this.request<AnalyticsSnapshot[]>(
      'GET',
      `/workspaces/${workspaceId}/analytics/snapshots`,
      undefined,
      { limit },
    )
  }

  getEnvironmentReadiness(workspaceId: string) {
    return this.request<Record<string, unknown>>(
      'GET',
      `/workspaces/${workspaceId}/environment`,
    )
  }

  listMarketClusters(workspaceId: string, limit?: number) {
    return this.request<Record<string, unknown>[]>(
      'GET',
      `/workspaces/${workspaceId}/market/clusters`,
      undefined,
      { limit },
    )
  }

  listMarketOpportunities(workspaceId: string, cpChannelId?: string, limit?: number) {
    return this.request<MarketOpportunity[]>(
      'GET',
      `/workspaces/${workspaceId}/market/opportunities`,
      undefined,
      { cp_channel_id: cpChannelId, limit },
    )
  }

  listMarketExperiments(workspaceId: string, cpChannelId?: string, limit?: number) {
    return this.request<MarketExperiment[]>(
      'GET',
      `/workspaces/${workspaceId}/market/experiments`,
      undefined,
      { cp_channel_id: cpChannelId, limit },
    )
  }

  listStrategyBriefs(workspaceId: string, cpChannelId: string, status?: string) {
    return this.request<ExperimentStrategyBrief[]>(
      'GET',
      `/workspaces/${workspaceId}/market/strategy-briefs`,
      undefined,
      { cp_channel_id: cpChannelId, status },
    )
  }

  getCrossPublicationLearning(workspaceId: string, channelId: string) {
    return this.request<CrossPublicationLearning>(
      'GET',
      `/workspaces/${workspaceId}/channels/${channelId}/cross-publication`,
    )
  }

  getOpportunityEvidence(workspaceId: string, opportunityId: number, cpChannelId: string) {
    return this.request<OpportunityEvidenceResponse>(
      'GET',
      `/workspaces/${workspaceId}/market/opportunities/${opportunityId}/evidence`,
      undefined,
      { cp_channel_id: cpChannelId },
    )
  }

  // ── Publications ─────────────────────────────────────────────────────────

  listPublications(workspaceId: string) {
    return this.request<PublicationListItem[]>('GET', `/workspaces/${workspaceId}/publications`)
  }

  getPublication(workspaceId: string, publicationId: number) {
    return this.request<PublicationDetail>('GET', `/workspaces/${workspaceId}/publications/${publicationId}`)
  }

  getPublicationVisualQuality(workspaceId: string, publicationId: number) {
    return this.request<PublicationVisualQuality>(
      'GET',
      `/workspaces/${workspaceId}/publications/${publicationId}/visual-quality`,
    )
  }

  getPublicationAnalytics(workspaceId: string, publicationId: number) {
    return this.request<PublicationAnalytics>('GET', `/workspaces/${workspaceId}/publications/${publicationId}/analytics`)
  }

  getPublicationAnalyticsHistory(workspaceId: string, publicationId: number) {
    return this.request<PublicationAnalyticsHistoryEntry[]>(
      'GET',
      `/workspaces/${workspaceId}/publications/${publicationId}/analytics/history`,
    )
  }

  releasePublic(workspaceId: string, publicationId: number) {
    return this.request<{ visibility: string; reconciled: boolean }>(
      'POST',
      `/workspaces/${workspaceId}/publications/${publicationId}/release-public`,
    )
  }

  async streamPublication(workspaceId: string, publicationId: number): Promise<Blob> {
    const token = this._auth?.getToken() ?? null
    const path = `/workspaces/${workspaceId}/publications/${publicationId}/stream`
    const url = new URL(BASE_URL + path, window.location.origin)
    const res = await fetch(url.toString(), {
      method: 'GET',
      headers: this._buildHeaders(token),
      signal: AbortSignal.timeout(120_000),
    })
    if (res.status === 403) throw new ForbiddenError()
    if (!res.ok) {
      let detail = `HTTP ${res.status}`
      try {
        const err = await res.json()
        detail = err.detail ?? detail
      } catch { /* ignore */ }
      throw new ApiError(res.status, detail)
    }
    return res.blob()
  }

  // ── Learning ─────────────────────────────────────────────────────────────

  listRecommendations(
    workspaceId: string,
    status?: string,
    domain?: string,
    publicationId?: number,
  ) {
    return this.request<OptimizationRecommendation[]>(
      'GET',
      `/workspaces/${workspaceId}/recommendations`,
      undefined,
      { status, domain, publication_id: publicationId },
    )
  }

  acceptRecommendation(
    workspaceId: string,
    recommendationId: number,
    body?: { notes?: string; expected_outcome?: string },
  ) {
    return this.request<RecommendationReviewEvent>(
      'POST',
      `/workspaces/${workspaceId}/recommendations/${recommendationId}/accept`,
      body,
    )
  }

  rejectRecommendation(
    workspaceId: string,
    recommendationId: number,
    body: { notes: string; expected_outcome?: string },
  ) {
    return this.request<RecommendationReviewEvent>(
      'POST',
      `/workspaces/${workspaceId}/recommendations/${recommendationId}/reject`,
      body,
    )
  }
}

export const api = new ApiClient()
