/* Typed contracts mirroring ApplicationService view models.
   Backend remains canonical — these types must match contracts.py exactly. */

export interface WorkspaceView {
  id: string
  name: string
  slug: string
  status: string
  organization_id: string | null
  channel_count: number
  active_pipeline_count: number
  paused: boolean
  automation_level: string
}

export interface ChannelView {
  id: string
  workspace_id: string
  name: string
  slug: string
  status: string
  paused: boolean
  account_count: number
  automation_level: string
  active_pipeline_count: number
}

export interface AccountView {
  id: string
  channel_id: string
  platform_key: string
  display_name: string
  status: string
  external_account_id: string
  automation_level: string
}

export interface PipelineStageView {
  stage: string
  attempt_number: number
  status: string
  artifact_id: string | null
  artifact_type: string | null
  error_message: string | null
  duration_ms: number | null
  started_at: string | null
  completed_at: string | null
}

export interface PipelineView {
  id: string
  workspace_id: string
  channel_id: string | null
  platform_account_id: string | null
  topic_id: number | null
  correlation_id: string
  idempotency_key: string
  status: string
  current_stage: string | null
  end_stage: string
  experiment_id: string | null
  actor: string
  error_message: string | null
  blocked_reason: string | null
  stages: PipelineStageView[]
  created_at: string
  updated_at: string
  contract_version: string
}

export interface OperationView {
  id: string
  workspace_id: string
  channel_id: string | null
  platform_account_id: string | null
  operation_type: string
  status: string
  idempotency_key: string
  actor: string
  correlation_id: string | null
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface ScheduleView {
  id: string
  workspace_id: string
  channel_id: string | null
  name: string
  operation_type: string
  schedule_type: string
  schedule_config: Record<string, unknown>
  timezone: string
  is_active: boolean
  last_run_at: string | null
  next_run_at: string | null
  actor: string
  created_at: string
  updated_at: string
}

export interface ReviewItemView {
  item_type: string
  item_id: string
  workspace_id: string
  channel_id: string | null
  description: string
  status: string
  created_at: string
  metadata: Record<string, unknown>
}

export interface ExceptionView {
  exception_type: string
  entity_id: string
  workspace_id: string
  description: string
  severity: string
  occurred_at: string
  metadata: Record<string, unknown>
}

export interface HealthView {
  workspace_id: string
  overall_status: string
  event_bus_ok: boolean
  dead_letter_count: number
  stuck_operation_count: number
  active_pipeline_count: number
  budget_status: string
  details: Record<string, unknown>
}

export interface CostView {
  workspace_id: string
  period: string
  total_usd: number
  channel_breakdown: Array<Record<string, unknown>>
  budget_policies: Array<Record<string, unknown>>
  warning_active: boolean
  block_active: boolean
}

export interface AuditView {
  event_type: string
  actor: string
  timestamp: string
  correlation_id: string | null
  description: string
  metadata: Record<string, unknown>
}

export interface DiagnosticFinding {
  category: string
  severity: string
  message: string
  detail: Record<string, unknown>
}

export interface DiagnosticReport {
  subject: string
  subject_id: string
  workspace_id: string
  status: string
  findings: DiagnosticFinding[]
  generated_at: string
  contract_version: string
}

/* Control Plane types from CP services — match actual model fields exactly */
export interface CPWorkspace {
  id: string
  name: string
  slug: string
  status: string
  organization_id: string | null
  actor: string
  created_at: string
  updated_at: string
  metadata_json: string | null
}

export interface CPChannel {
  id: string
  workspace_id: string
  name: string
  slug: string
  status: string
  actor: string
  created_at: string
  updated_at: string
  description: string | null
  metadata_json: string | null
}

export interface CPAccount {
  id: string
  channel_id: string
  platform_id: string
  platform_key: string
  external_account_id: string
  display_name: string
  status: string
  credential_profile_id: string | null
  actor: string
  created_at: string
  updated_at: string
  metadata_json: string | null
}

export interface StrategyProfile {
  id: string
  workspace_id: string
  name: string
  version: number
  status: string
  config_json: Record<string, unknown>
  created_at: string
}

export interface ControlEvent {
  id: string
  workspace_id: string
  event_type: string
  payload_json: Record<string, unknown>
  actor: string
  correlation_id: string | null
  created_at: string
}

export interface Experiment {
  id: string
  workspace_id: string
  name: string
  hypothesis: string
  status: string
  primary_metric: string
  start_at: string | null
  end_at: string | null
  created_at: string
}

export interface YouTubeVerificationResult {
  account_id: string
  verified: boolean
  registered_channel_id: string
  live_channel_id: string | null
  channel_title: string | null
  verified_at: string | null
  failure_reason: string | null
}

/* Analytics */
export interface AnalyticsAggregate {
  id: number
  publication_id: number
  topic_id: number
  provider: string
  period_type: string
  period_key: string
  metric_name: string
  metric_value: number
  snapshot_count: number
  calculation_method: string
  currency_code: string | null
  source_snapshot_ids_json: string
  input_hash: string
  created_at: string
}

export interface AnalyticsSnapshot {
  id: number
  publication_id: number
  topic_id: number
  provider: string
  period_start: string | null
  period_end: string | null
  is_period_complete: number
  currency_code: string | null
  ingested_at: string
  created_at: string
}

/* Learning */
export interface OptimizationRecommendation {
  id: number
  learning_run_id: number
  topic_id: number
  publication_id: number | null
  domain: string
  subsystem: string
  measure: string
  title: string
  explanation: string
  expected_improvement: string
  evidence_json: string
  evidence_classification: string
  recommendation_strength: string
  confidence: string
  confidence_score: number
  affected_subsystem: string
  subsystem_entity_type: string
  subsystem_entity_id: number | null
  experiment_id: string | null
  engine_version: string
  schema_version: string
  input_hash: string
  status: string
  superseded_at: string | null
  superseded_by_id: number | null
  created_at: string
}

export interface RecommendationReviewEvent {
  id: number
  recommendation_id: number
  topic_id: number
  event_type: string
  reviewer: string
  notes: string
  expected_outcome: string
  input_hash: string
  created_at: string
}

/* Topics */
export interface TopicView {
  id: number
  title: string
  angle: string
  status: string
  workspace_id: string | null
  created_at: string
  updated_at: string
}

/* Stage artifact — from GET /pipelines/{id}/stages/{stage}/artifact */
export interface StageArtifact {
  pipeline_id: string
  stage: string
  workspace_id: string
  artifact_id: string | null
  artifact_type: string | null
  stage_status: string
  attempt_number: number
  started_at: string | null
  completed_at: string | null
  duration_ms: number | null
  error_message: string | null
  resolved: boolean
  reason?: string
  content_type?: string
  content?: Record<string, unknown>
  truncated?: boolean
}

/* API error shape */
export interface ApiError {
  detail: string
  status: number
}

// ── Publications ─────────────────────────────────────────────────────────────

export interface PublicationListItem {
  id: number
  title: string
  provider: string
  provider_video_id: string | null
  provider_url: string | null
  visibility: string
  status: string
  published_at: string | null
  render_manifest_id: number | null
  created_at: string
}

export interface PublicationDetail {
  id: number
  title: string
  description: string
  tags: string[]
  provider: string
  provider_video_id: string | null
  provider_url: string | null
  visibility: string
  status: string
  published_at: string | null
  render_manifest_id: number | null
  render_duration_ms: number | null
  render_width: number | null
  render_height: number | null
  render_fps: number | null
  render_status: string | null
  render_approved_at: string | null
  created_at: string
  /** Structural preconditions met: youtube + published + private + has video ID + has account */
  release_eligible: boolean
  /** Both ACE_RELEASE_PUBLIC_ENABLED and ACE_PUBLISHING_LIVE_ENABLED are true */
  release_enabled: boolean
  /** Stored OAuth token includes youtube.force-ssl (read-only check, no network call) */
  release_scope_granted: boolean
}

export interface PublicationAnalytics {
  snapshot_id: number | null
  snapshot_ingested_at: string | null
  period_start: string | null
  period_end: string | null
  metrics: Record<string, number>
  retention_point_count: number
}
