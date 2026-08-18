/* Test fixtures matching backend view model contracts */

import type {
  AnalyticsAggregate,
  CPWorkspace,
  CPChannel,
  CPAccount,
  PublicationAnalytics,
  PublicationDetail,
  PublicationListItem,
  WorkspaceView,
  HealthView,
  OptimizationRecommendation,
  PipelineView,
  ReviewItemView,
  ExceptionView,
  CostView,
  AuditView,
  OperationView,
  DiagnosticReport,
  Experiment,
  ControlEvent,
  TopicView,
  StageArtifact,
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

export const analyticsAggregate: AnalyticsAggregate = {
  id: 1,
  publication_id: 1,
  topic_id: 10,
  provider: 'youtube_test',
  period_type: 'lifetime',
  period_key: 'lifetime',
  metric_name: 'views',
  metric_value: 42000,
  snapshot_count: 5,
  calculation_method: 'sum',
  currency_code: null,
  source_snapshot_ids_json: '[1,2,3,4,5]',
  input_hash: 'hash-agg-001',
  created_at: '2025-01-01T00:00:00',
}

export const analyticsAggregateCtr: AnalyticsAggregate = {
  ...analyticsAggregate,
  id: 2,
  metric_name: 'ctr',
  metric_value: 0.047,
  calculation_method: 'latest_observation',
  input_hash: 'hash-agg-002',
}

export const recommendation: OptimizationRecommendation = {
  id: 1,
  learning_run_id: 1,
  topic_id: 10,
  publication_id: null,
  domain: 'scripts',
  subsystem: 'hook',
  measure: 'ctr',
  title: 'Shorter hooks are associated with higher CTR',
  explanation: 'Topics with hooks under 5s show CTR 0.047 vs 0.031 for longer hooks across 5 snapshots.',
  expected_improvement: 'Shortening hooks is associated with improved CTR.',
  evidence_json: '[]',
  evidence_classification: 'observational',
  recommendation_strength: 'actionable',
  confidence: 'medium',
  confidence_score: 0.62,
  affected_subsystem: '',
  subsystem_entity_type: '',
  subsystem_entity_id: null,
  experiment_id: null,
  engine_version: '1.0',
  schema_version: '1',
  input_hash: 'hash-rec-001',
  status: 'pending',
  superseded_at: null,
  superseded_by_id: null,
  created_at: '2025-01-01T00:00:00',
}

export const recommendationAccepted: OptimizationRecommendation = {
  ...recommendation,
  id: 2,
  title: 'Slower pacing is associated with higher retention',
  status: 'accepted',
  domain: 'narration',
  subsystem: 'pacing',
  measure: 'average_view_duration',
  confidence: 'high',
  confidence_score: 0.81,
  input_hash: 'hash-rec-002',
}

export const TOPIC_ID = 7

export const topicView: TopicView = {
  id: TOPIC_ID,
  title: 'AI in Healthcare',
  angle: 'Focus on diagnostics use-cases',
  status: 'active',
  workspace_id: WS_ID,
  created_at: '2025-06-01T10:00:00',
  updated_at: '2025-06-01T10:00:00',
}

export const topicView2: TopicView = {
  id: 8,
  title: 'Future of Renewable Energy',
  angle: '',
  status: 'active',
  workspace_id: WS_ID,
  created_at: '2025-06-02T09:00:00',
  updated_at: '2025-06-02T09:00:00',
}

export const stageArtifactResolved: StageArtifact = {
  pipeline_id: PIPE_ID,
  stage: 'research',
  workspace_id: WS_ID,
  artifact_id: 'art-001',
  artifact_type: 'research_brief',
  stage_status: 'completed',
  attempt_number: 1,
  started_at: '2025-01-01T00:01:00',
  completed_at: '2025-01-01T00:01:01',
  duration_ms: 1200,
  error_message: null,
  resolved: true,
  content_type: 'research_brief',
  content: { query: 'AI in Healthcare', summary: 'Brief summary of research findings' },
  truncated: false,
}

export const stageArtifactUnresolved: StageArtifact = {
  pipeline_id: PIPE_ID,
  stage: 'narration',
  workspace_id: WS_ID,
  artifact_id: null,
  artifact_type: null,
  stage_status: 'running',
  attempt_number: 1,
  started_at: null,
  completed_at: null,
  duration_ms: null,
  error_message: null,
  resolved: false,
  reason: 'no_artifact',
}

export const PUB_ID = 1

export const publicationListItem: PublicationListItem = {
  id: PUB_ID,
  title: 'Why Renewable Energy Is Getting So Cheap',
  provider: 'youtube',
  provider_video_id: 'kQH88nXdiRY',
  provider_url: 'https://www.youtube.com/watch?v=kQH88nXdiRY',
  visibility: 'private',
  status: 'published',
  published_at: '2026-08-17T21:21:38',
  render_manifest_id: 4,
  created_at: '2026-08-17T21:21:38',
}

export const publicationDetail: PublicationDetail = {
  id: PUB_ID,
  title: 'Why Renewable Energy Is Getting So Cheap',
  description: 'An in-depth look at the economics of renewable energy.',
  tags: ['energy', 'tech', 'sustainability'],
  provider: 'youtube',
  provider_video_id: 'kQH88nXdiRY',
  provider_url: 'https://www.youtube.com/watch?v=kQH88nXdiRY',
  visibility: 'private',
  status: 'published',
  published_at: '2026-08-17T21:21:38',
  render_manifest_id: 4,
  render_duration_ms: 58607,
  render_width: 1080,
  render_height: 1920,
  render_fps: 30,
  render_status: 'approved',
  render_approved_at: '2026-08-17T17:41:14',
  created_at: '2026-08-17T21:21:38',
  release_eligible: true,
  release_enabled: false,
  release_scope_granted: false,
}

export const publicationAnalytics: PublicationAnalytics = {
  snapshot_id: 1,
  snapshot_ingested_at: '2026-08-18T01:19:39',
  period_start: '2026-08-10',
  period_end: '2026-08-17',
  metrics: { views: 1234, ctr: 0.047 },
  retention_point_count: 0,
}

export const stageDiagnosticReport: DiagnosticReport = {
  subject: 'pipeline',
  subject_id: PIPE_ID,
  workspace_id: WS_ID,
  status: 'warn',
  findings: [
    {
      category: 'pipeline_stage',
      severity: 'warning',
      message: 'Stage running longer than expected',
      detail: { stage: 'research', elapsed_ms: 3500 },
    },
  ],
  generated_at: '2025-01-01T12:00:00',
  contract_version: '1.0',
}
