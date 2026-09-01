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

/* Channel Strategy Profile (Phase 17E). config_json is a JSON-encoded
   string on the wire — parse with JSON.parse before use, mirroring the
   backend's StrategyProfile.config property. */
export interface StrategyProfile {
  id: string
  channel_id: string
  version: number
  config_json: string
  actor: string
  created_at: string
  is_active: boolean
}

export interface StrategyRegimeConfig {
  market_intelligence_weight: number
  channel_evidence_weight: number
  exploration_share: number
  /** Only present on the 'bootstrap' regime. */
  target_publication_count?: number
}

/** The structured shape of StrategyProfile.config_json once parsed. No
    topic names appear anywhere in this schema — candidates are sourced
    dynamically from live market intelligence, always. */
export interface StrategyConfig {
  schema_version: string
  bootstrap: StrategyRegimeConfig
  steady_state: StrategyRegimeConfig
  transition: {
    trigger_metric: string
    maturity_threshold: string
  }
  diversity: {
    max_cluster_share: number
    max_consecutive_same_cluster: number
  }
  creative_dimensions: string[]
  total_portfolio_slots: number
}

/** The channel's ACTUAL effective regime right now, computed from real
    channel_performance_baselines maturity — never taken from stored
    config, so it can never claim a transition that hasn't happened. */
export interface StrategyEffectiveState {
  trigger_metric: string
  maturity_threshold: string
  current_maturity: string
  publication_count: number
  effective_regime: 'bootstrap' | 'steady_state'
  market_intelligence_weight: number | null
  channel_evidence_weight: number | null
  exploration_share: number | null
}

export interface ChannelStrategyResponse {
  status: 'ok' | 'unavailable'
  message?: string
  profile: StrategyProfile | null
  effective: StrategyEffectiveState | null
  config_errors?: string[] | null
}

export type ReadinessStatus = 'ready' | 'degraded' | 'blocked'

export type ReadinessCategoryKey =
  | 'decision'
  | 'production'
  | 'analytics_learning'
  | 'provider_oauth'
  | 'publishing_authorization'
  | 'scheduler'

export interface AutonomyReadinessCheck {
  key: string
  label: string
  /** Retained for compatibility; equivalent to status === 'ready'. */
  ready: boolean
  detail: string
  status: ReadinessStatus
  category: ReadinessCategoryKey
}

export interface AutonomyReadinessCategory {
  key: ReadinessCategoryKey
  label: string
  /** Worst status among the category's checks. */
  status: ReadinessStatus
  check_keys: string[]
}

export interface AutonomyReadinessResponse {
  channel_id: string
  checks: AutonomyReadinessCheck[]
  ready_for_decision_automation: boolean
  authorized_for_public_publishing: boolean
  categories: AutonomyReadinessCategory[]
  overall_status: ReadinessStatus
}

export type CadenceType = 'every_12h' | 'daily' | 'every_n_days' | 'weekly' | 'custom_cron'
export type PublishingSlotState = 'reserved' | 'filled' | 'cancelled' | 'expired'

export type ProductionStatus = 'queued' | 'producing' | 'ready' | 'failed'

export interface AutonomyPolicy {
  channel_id: string
  workspace_id: string
  decision_automation_enabled: boolean
  production_automation_enabled: boolean
  cadence_type: CadenceType
  cadence_interval_days: number | null
  cadence_cron: string | null
  preferred_local_hour: number
  timezone: string | null
  queue_target: number
  market_refresh_max_age_hours: number
  semantic_fit_max_evaluations_per_run: number
  last_decision_at: string | null
  last_decision_outcome: string | null
  actor: string
  created_at: string | null
  updated_at: string | null
}

export interface PublishingSlot {
  id: number
  channel_id: string
  workspace_id: string
  slot_key: string
  scheduled_for_local: string
  timezone: string
  scheduled_for_utc: string
  state: PublishingSlotState
  brief_id: string | null
  selection_decision_id: number | null
  opportunity_id: number | null
  reserved_at: string
  filled_at: string | null
  cancelled_at: string | null
  cancellation_reason: string | null
  experiment_id: string | null
  production_status: ProductionStatus | null
  production_pipeline_id: string | null
  production_publishing_plan_id: number | null
  production_started_at: string | null
  production_ready_at: string | null
  production_failed_at: string | null
  production_failed_stage: string | null
  production_error: string | null
  production_retry_count: number
  publish_status: PublishStatus | null
  publication_id: number | null
  publish_provider_video_id: string | null
  publish_started_at: string | null
  publish_uploaded_at: string | null
  publish_released_at: string | null
  publish_failed_at: string | null
  publish_failure_category: string | null
  publish_error: string | null
  publish_retry_count: number
  rescheduled_from_slot_id: number | null
}

export interface ChannelAutomationPolicyResponse {
  policy: AutonomyPolicy | null
  active_slots: PublishingSlot[]
}

/* Phase 18C — public-publishing authorization */

export type PublishStatus =
  | 'pending' | 'publishing' | 'uploaded' | 'released'
  | 'failed' | 'skipped_missed' | 'blocked'

export type PublishingBlockReason =
  | 'global_publishing_gate_off'
  | 'global_release_gate_off'
  | 'channel_not_authorized'
  | 'rate_limit_reached'
  | 'account_unhealthy'
  | 'no_account'
  | 'release_scope_missing'

export interface ChannelPublishingAuthorization {
  channel_id: string
  workspace_id: string
  authorized: boolean
  authorized_at: string | null
  authorized_by: string | null
  revoked_at: string | null
  revoked_by: string | null
  policy_version: number
  max_publications_per_24h: number
  missed_slot_grace_minutes: number
  created_at: string | null
  updated_at: string | null
}

export interface PublishingAuthorizationDecision {
  allowed: boolean
  blocked_by: PublishingBlockReason[]
  detail: string
  global_publishing_enabled: boolean
  global_release_enabled: boolean
  channel_authorized: boolean
  publications_last_24h: number
  max_publications_per_24h: number
  account_id: string | null
  account_status: string | null
  release_scope_granted: boolean
}

export interface ChannelPublishingAuthorizationResponse {
  authorization: ChannelPublishingAuthorization | null
  decision: PublishingAuthorizationDecision
}

export interface UpdatePublishingAuthorizationRequest {
  authorized?: boolean
  confirm?: boolean
  reason?: string
  max_publications_per_24h?: number
  missed_slot_grace_minutes?: number
}

export interface UpdateAutomationPolicyRequest {
  decision_automation_enabled?: boolean
  production_automation_enabled?: boolean
  cadence_type?: CadenceType
  cadence_interval_days?: number | null
  timezone?: string
  preferred_local_hour?: number
  queue_target?: number
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

/* Cross-publication learning (Phase 17D) — channel_performance_baselines
   and feature_performance_observations. Both use the same 4-tier
   sample_maturity vocabulary as MaturityBadge (insufficient/exploratory/
   directional/actionable), scaled by publication count — a DIFFERENT
   vocabulary and threshold from OptimizationRecommendation's
   recommendation_strength/confidence pair. Do not merge the two. */
export interface ChannelPerformanceBaseline {
  id: number
  channel_id: string
  workspace_id: string | null
  metric_name: string
  period_type: string
  publication_count: number
  mean: number | null
  median: number | null
  min_value: number | null
  max_value: number | null
  std_dev: number | null
  sample_maturity: string
  source_publication_ids_json: string
  source_snapshot_ids_json: string
  comparison_schema_version: string
  observer_version: string
  input_hash: string
  created_at: string
  updated_at: string
}

export interface FeaturePerformanceObservation {
  id: number
  channel_id: string
  workspace_id: string | null
  feature_name: string
  feature_bucket: string
  metric_name: string
  period_type: string
  publication_count: number
  mean: number | null
  median: number | null
  min_value: number | null
  max_value: number | null
  std_dev: number | null
  baseline_mean: number | null
  baseline_median: number | null
  abs_diff_from_baseline: number | null
  rel_diff_from_baseline: number | null
  sample_maturity: string
  /** Always 'association' — this table never records a causal claim. */
  observation_type: string
  source_publication_ids_json: string
  source_snapshot_ids_json: string
  comparison_schema_version: string
  observer_version: string
  input_hash: string
  created_at: string
  updated_at: string
}

export interface CrossPublicationLearning {
  channel_id: string
  baselines: ChannelPerformanceBaseline[]
  feature_observations: FeaturePerformanceObservation[]
}

/* Market intelligence (Phase 17D) — external YouTube opportunity signals,
   distinct from Orvella's own performance evidence above. Sub-scores and
   their status companions can be legitimately absent (no evidence yet) —
   null must never be treated as zero. score_competition is INVERTED: a
   HIGH score means LOW competition (more attractive), not the reverse. */
export interface MarketOpportunity {
  id: number
  channel_id: number
  normalized_topic: string
  raw_topic: string
  title: string
  topic_summary: string
  format_recommendation: string
  strategic_role: string
  current_lifecycle_state: string
  canonical_cluster_id: number | null
  created_at: string
  canonical_label: string | null
  evidence_count: number
  composite_score: number | null
  confidence: number | null
  score_trend_strength: number | null
  score_audience_demand: number | null
  /** Inverted: higher = less competition. */
  score_competition: number | null
  score_evergreen_value: number | null
  score_audience_fit: number | null
  score_content_novelty: number | null
  status_trend_strength: string
  status_audience_demand: string
  status_competition: string
  status_evergreen_value: string
  status_audience_fit: string
  status_content_novelty: string
}

/* Evidence drill-down (Phase 17F) — EXTERNAL MARKET EVIDENCE for one
   opportunity, grouped by the sync snapshot that produced each batch.
   Never raw provider payloads. */
export interface OpportunityEvidenceItem {
  evidence_type: string
  label: string
  value: number | null
  text: string | null
  unit: string | null
}

export interface OpportunityEvidenceSnapshot {
  source_label: string
  collected_at: string
  items: OpportunityEvidenceItem[]
}

export interface OpportunityEvidenceResponse {
  opportunity_id: number
  evidence_count: number
  snapshots: OpportunityEvidenceSnapshot[]
}

export interface MarketExperiment {
  id: string
  channel_id: number
  opportunity_id: number | null
  experiment_type: string
  hypothesis: string
  status: string
  created_at: string
}

export interface TreatmentFactorSpec {
  factor_name: string
  autonomy: string
  intended_value: number | string | null
  safe_range_min: number | null
  safe_range_max: number | null
}

export interface ControlledFactorBaseline {
  factor_name: string
  baseline_value: number | string | null
  baseline_source: string
  tolerance: string | null
}

export interface ContentConstraints {
  excluded_topics: string[]
  primary_niche: string
  secondary_niches: string[]
  brand_voice: string
  content_style: string
  audience_description: string
}

/** The experiment planner's "what to try next" handoff artifact — real
    deterministic reasoning fields (strategic_reason, information_gain_reason),
    not free-text generated copy. status='pending_approval' by default: a
    proposed next experiment for a human to review, never auto-applied. */
export interface ExperimentStrategyBrief {
  id: string
  channel_id: number
  planning_run_id: string
  selection_decision_id: number
  opportunity_id: number
  canonical_cluster_id: number | null
  channel_profile_version_id: number | null
  brief_planning_intent: string
  experiment_type: string
  market_theme: string
  canonical_topic: string
  strategic_reason: string
  information_gain_reason: string
  hypothesis: string
  target_metric: string
  target_direction: string
  treatment_factors: TreatmentFactorSpec[]
  controlled_factors: ControlledFactorBaseline[]
  content_constraints: ContentConstraints
  confounding_risk: string
  policy_version: string
  eligibility_classification: string
  score_decomposition_json: string
  brief_hash: string
  status: string
  created_at: string
  linked_experiment: {
    id: string
    status: string
    experiment_type: string
    created_at: string
  } | null
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
  render_duration_ms: number | null
  topic_title: string | null
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
  /** Topic title, if the publishing plan's topic is resolvable. Null when the
      topic record is missing or not linked (a known backend data gap — see
      Phase 17B report). */
  topic_title: string | null
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
  /** Experiment this observation is associated with, when canonical lineage
      supports it. Null for the vast majority of publications today. */
  experiment_id: string | null
}

/** One analytics observation for a publication, oldest-first. `metrics` is
    `{}` when the provider had nothing to report yet (observation_state
    'no_data') — never a fabricated zero. See GET .../analytics/history. */
export interface PublicationAnalyticsHistoryEntry {
  snapshot_id: number
  ingested_at: string
  observed_at: string | null
  period_start: string | null
  period_end: string | null
  observation_state: 'data' | 'no_data' | null
  experiment_id: string | null
  metrics: Record<string, number>
}

// ── Phase 18E — visual quality ──────────────────────────────────────────────

export type VisualQualityStatus = 'pass' | 'pass_with_warnings' | 'blocked'

export interface VisualQualityFinding {
  code: string
  severity: 'warning' | 'blocking'
  message: string
  evidence?: Record<string, unknown>
}

export interface VisualSceneDiagnostic {
  beat_index: number
  scene_index: number
  start_ms: number
  duration_ms: number
  visual_intent: string
  planned: string
  realized: string
  meaningful: boolean
  provider: string | null
  fallback_reason: string | null
  fallback_class: 'none' | 'creative' | 'provider'
}

export interface VisualFamilySlice {
  family: string
  beat_count: number
  runtime_ms: number
  runtime_pct: number
}

export interface PublicationVisualQuality {
  assessed: boolean
  status?: VisualQualityStatus
  assessment_version?: string
  policy_version?: string
  visual_style?: string | null
  total_beat_count?: number
  total_duration_ms?: number
  scene_count?: number
  meaningful_runtime_pct?: number
  text_card_runtime_pct?: number
  meaningful_beat_count?: number
  visual_changes_per_minute?: number
  distinct_asset_count?: number
  asset_reuse_ratio?: number
  max_meaningful_gap_ms?: number
  avg_meaningful_gap_ms?: number
  opening_meaningful_visual?: boolean
  dominant_family?: string | null
  dominant_family_share?: number
  family_diversity?: number
  family_distribution?: VisualFamilySlice[]
  fallback_beat_count?: number
  provider_fallback_beats?: number
  creative_fallback_beats?: number
  provider_fallback_rate?: number
  fallback_reasons?: Record<string, number>
  planned_meaningful_beats?: number
  remediation_attempts?: number
  remediated?: boolean
  findings?: VisualQualityFinding[]
  scene_diagnostics?: VisualSceneDiagnostic[]
  assessed_at?: string
}
