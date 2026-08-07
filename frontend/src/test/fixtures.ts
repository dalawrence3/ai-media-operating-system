/* Test fixtures matching backend view model contracts */

import type {
  CPWorkspace,
  CPChannel,
  CPAccount,
  WorkspaceView,
  HealthView,
  PipelineView,
  ReviewItemView,
  ExceptionView,
  CostView,
  AuditView,
  OperationView,
  DiagnosticReport,
  Experiment,
  ControlEvent,
} from '@/api/types'

export const WS_ID = 'ws-test-001'
export const CH_ID = 'ch-test-001'
export const CH_ID_2 = 'ch-test-002'
export const ACC_ID = 'acc-test-001'
export const ACC_ID_2 = 'acc-test-002'
export const PIPE_ID = 'pipe-test-001'

export const cpWorkspace: CPWorkspace = {
  id: WS_ID,
  name: 'Test Workspace',
  slug: 'test-workspace',
  status: 'active',
  organization_id: null,
  actor: 'system:init',
  created_at: '2025-01-01T00:00:00',
  updated_at: '2025-01-01T00:00:00',
  metadata_json: null,
}

export const workspaceView: WorkspaceView = {
  id: WS_ID,
  name: 'Test Workspace',
  slug: 'test-workspace',
  status: 'active',
  organization_id: null,
  channel_count: 2,
  active_pipeline_count: 1,
  paused: false,
  automation_level: 'MANUAL',
}

export const healthView: HealthView = {
  workspace_id: WS_ID,
  overall_status: 'healthy',
  event_bus_ok: true,
  dead_letter_count: 0,
  stuck_operation_count: 0,
  active_pipeline_count: 1,
  budget_status: 'ok',
  details: {},
}

export const cpChannel: CPChannel = {
  id: CH_ID,
  workspace_id: WS_ID,
  name: 'Channel Alpha',
  slug: 'channel-alpha',
  status: 'active',
  actor: 'system:init',
  created_at: '2025-01-01T00:00:00',
  updated_at: '2025-01-01T00:00:00',
  description: 'First test channel',
  metadata_json: null,
}

export const cpChannel2: CPChannel = {
  id: CH_ID_2,
  workspace_id: WS_ID,
  name: 'Channel Beta',
  slug: 'channel-beta',
  status: 'active',
  actor: 'system:init',
  created_at: '2025-01-01T00:00:00',
  updated_at: '2025-01-01T00:00:00',
  description: 'Second test channel',
  metadata_json: null,
}

// Two accounts on the same platform — must remain independent
export const cpAccount1: CPAccount = {
  id: ACC_ID,
  channel_id: CH_ID,
  platform_id: 'youtube',
  platform_key: 'youtube',
  external_account_id: 'UCaaa111',
  display_name: 'Account A',
  status: 'active',
  credential_profile_id: 'cred-001',
  actor: 'system:init',
  created_at: '2025-01-01T00:00:00',
  updated_at: '2025-01-01T00:00:00',
  metadata_json: null,
}

export const cpAccount2: CPAccount = {
  id: ACC_ID_2,
  channel_id: CH_ID,
  platform_id: 'youtube',
  platform_key: 'youtube',
  external_account_id: 'UCbbb222',
  display_name: 'Account B',
  status: 'paused',
  credential_profile_id: null,
  actor: 'system:init',
  created_at: '2025-01-01T00:00:00',
  updated_at: '2025-01-01T00:00:00',
  metadata_json: null,
}

export const pipelineView: PipelineView = {
  id: PIPE_ID,
  workspace_id: WS_ID,
  channel_id: CH_ID,
  platform_account_id: ACC_ID,
  topic_id: null,
  correlation_id: 'corr-001',
  idempotency_key: 'idem-001',
  status: 'running',
  current_stage: 'script_generation',
  end_stage: 'publishing',
  experiment_id: null,
  actor: 'dev:studio-user',
  error_message: null,
  blocked_reason: null,
  stages: [
    {
      stage: 'research',
      attempt_number: 1,
      status: 'completed',
      artifact_id: 'art-001',
      artifact_type: 'research_brief',
      error_message: null,
      duration_ms: 1200,
      started_at: '2025-01-01T00:01:00',
      completed_at: '2025-01-01T00:01:01',
    },
    {
      stage: 'script_generation',
      attempt_number: 1,
      status: 'running',
      artifact_id: null,
      artifact_type: null,
      error_message: null,
      duration_ms: null,
      started_at: '2025-01-01T00:01:05',
      completed_at: null,
    },
  ],
  created_at: '2025-01-01T00:00:00',
  updated_at: '2025-01-01T00:01:05',
  contract_version: '1.0',
}

export const pipelineBlocked: PipelineView = {
  ...pipelineView,
  id: 'pipe-blocked-001',
  status: 'blocked',
  current_stage: 'publishing',
  blocked_reason: 'provider_setup_required',
}

export const pipelineWaitingReview: PipelineView = {
  ...pipelineView,
  id: 'pipe-review-001',
  status: 'waiting_for_review',
  current_stage: 'script_generation',
}

export const pipelineFailed: PipelineView = {
  ...pipelineView,
  id: 'pipe-failed-001',
  status: 'failed',
  error_message: 'Provider returned 429: quota exceeded',
}

export const reviewItem: ReviewItemView = {
  item_type: 'script',
  item_id: 'script-review-001',
  workspace_id: WS_ID,
  channel_id: CH_ID,
  description: 'Script for "How AI Works" — needs review',
  status: 'pending',
  created_at: '2025-01-01T12:00:00',
  metadata: { pipeline_id: PIPE_ID },
}

export const reviewItem2: ReviewItemView = {
  item_type: 'narration',
  item_id: 'narr-review-001',
  workspace_id: WS_ID,
  channel_id: CH_ID,
  description: 'Narration audio — needs review',
  status: 'pending',
  created_at: '2025-01-01T13:00:00',
  metadata: {},
}

export const exceptionView: ExceptionView = {
  exception_type: 'budget_warning',
  entity_id: WS_ID,
  workspace_id: WS_ID,
  description: 'Monthly spend approaching limit',
  severity: 'warn',
  occurred_at: '2025-01-01T10:00:00',
  metadata: {},
}

export const costView: CostView = {
  workspace_id: WS_ID,
  period: 'monthly',
  total_usd: 12.34,
  channel_breakdown: [],
  budget_policies: [],
  warning_active: false,
  block_active: false,
}

export const auditView: AuditView = {
  event_type: 'pipeline_started',
  actor: 'dev:studio-user',
  timestamp: '2025-01-01T12:00:00',
  correlation_id: 'corr-001',
  description: 'Pipeline started for channel-alpha',
  metadata: {},
}

export const operationView: OperationView = {
  id: 'op-001',
  workspace_id: WS_ID,
  channel_id: CH_ID,
  platform_account_id: ACC_ID,
  operation_type: 'publish_video',
  status: 'completed',
  idempotency_key: 'idem-op-001',
  actor: 'dev:studio-user',
  correlation_id: 'corr-001',
  error_message: null,
  created_at: '2025-01-01T10:00:00',
  updated_at: '2025-01-01T10:05:00',
}

export const diagnosticReport: DiagnosticReport = {
  subject: 'workspace',
  subject_id: WS_ID,
  workspace_id: WS_ID,
  status: 'pass',
  findings: [
    {
      category: 'connectivity',
      severity: 'info',
      message: 'Event bus reachable',
      detail: {},
    },
  ],
  generated_at: '2025-01-01T12:00:00',
  contract_version: '1.0',
}

export const experiment: Experiment = {
  id: 'exp-001',
  workspace_id: WS_ID,
  name: 'Hook format A/B',
  hypothesis: 'Short hooks outperform long hooks in retention',
  status: 'active',
  primary_metric: 'retention_30s',
  start_at: '2025-01-01T00:00:00',
  end_at: null,
  created_at: '2025-01-01T00:00:00',
}

export const controlEvent: ControlEvent = {
  id: 'evt-001',
  workspace_id: WS_ID,
  event_type: 'pipeline_completed',
  payload_json: { pipeline_id: PIPE_ID },
  actor: 'dev:studio-user',
  correlation_id: 'corr-001',
  created_at: '2025-01-01T12:00:00',
}
