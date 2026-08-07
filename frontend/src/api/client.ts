/* Centralized typed API client.
   ALL backend interaction goes through this module.
   Do not scatter fetch() calls throughout components. */

import type {
  AccountView,
  AuditView,
  ChannelView,
  CostView,
  CPAccount,
  CPChannel,
  CPWorkspace,
  ControlEvent,
  DiagnosticReport,
  Experiment,
  ExceptionView,
  HealthView,
  OperationView,
  PipelineView,
  ReviewItemView,
  ScheduleView,
  StrategyProfile,
  WorkspaceView,
} from './types'

const BASE_URL = '/api/v1'

// DEV-ONLY actor header — replaced by JWT in Phase 15
const DEV_ACTOR = 'dev:studio-user'

class ApiClient {
  private async request<T>(
    method: string,
    path: string,
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
      headers: {
        'Content-Type': 'application/json',
        // DEV-ONLY — Phase 15 replaces with Authorization: Bearer <token>
        'X-Dev-Actor': DEV_ACTOR,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(30_000),
    })

    if (!res.ok) {
      let detail = `HTTP ${res.status}`
      try {
        const err = await res.json()
        detail = err.detail ?? detail
      } catch {
        // ignore parse failure
      }
      throw Object.assign(new Error(detail), { status: res.status })
    }

    return res.json() as Promise<T>
  }

  // ── Workspaces ──

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

  // ── Channels ──

  listChannels(workspaceId: string) {
    return this.request<CPChannel[]>('GET', `/workspaces/${workspaceId}/channels`)
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

  getChannelStrategy(workspaceId: string, channelId: string) {
    return this.request<StrategyProfile | { status: string; message: string }>(
      'GET',
      `/workspaces/${workspaceId}/channels/${channelId}/strategy`,
    )
  }

  getChannelPolicy(workspaceId: string, channelId: string) {
    return this.request<{ effective_automation_level: string }>(
      'GET',
      `/workspaces/${workspaceId}/channels/${channelId}/policy`,
    )
  }

  // ── Accounts ──

  getAccountSummary(workspaceId: string, accountId: string) {
    return this.request<AccountView>('GET', `/workspaces/${workspaceId}/accounts/${accountId}`)
  }

  // ── Pipelines ──

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

  // ── Reviews ──

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

  // ── Operations ──

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

  // ── Schedules ──

  listSchedules(workspaceId: string, isActive?: boolean) {
    return this.request<ScheduleView[]>(
      'GET',
      `/workspaces/${workspaceId}/schedules`,
      undefined,
      { is_active: isActive },
    )
  }

  // ── Diagnostics ──

  getDiagnostics(workspaceId: string, subject: string, subjectId: string) {
    return this.request<DiagnosticReport>(
      'GET',
      `/workspaces/${workspaceId}/diagnostics/${subject}/${subjectId}`,
    )
  }
}

export const api = new ApiClient()
