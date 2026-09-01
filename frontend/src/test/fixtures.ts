/* Test fixtures matching backend view model contracts */

import type {
  AnalyticsAggregate,
  AutonomyPolicy,
  AutonomyReadinessResponse,
  ChannelAutomationPolicyResponse,
  ChannelPerformanceBaseline,
  ChannelStrategyResponse,
  PublishingSlot,
  ChannelPublishingAuthorizationResponse,
  CPWorkspace,
  CPChannel,
  CPAccount,
  ExperimentStrategyBrief,
  FeaturePerformanceObservation,
  MarketExperiment,
  MarketOpportunity,
  OpportunityEvidenceResponse,
  PublicationAnalytics,
  PublicationAnalyticsHistoryEntry,
  PublicationDetail,
  PublicationListItem,
  StrategyConfig,
  StrategyProfile,
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
  ScheduleView,
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

export const marketOpportunity: MarketOpportunity = {
  id: 1,
  channel_id: 1,
  normalized_topic: 'crispr gene editing technology',
  raw_topic: 'CRISPR gene editing technology',
  title: 'CRISPR gene editing technology',
  topic_summary: '',
  format_recommendation: 'undecided',
  strategic_role: 'discovery',
  current_lifecycle_state: 'approved',
  canonical_cluster_id: 4,
  created_at: '2026-08-23T20:32:35',
  canonical_label: 'CRISPR gene editing technology',
  evidence_count: 22,
  composite_score: 0.62,
  confidence: 0.58,
  score_trend_strength: null,
  score_audience_demand: 0.61,
  score_competition: 0.42,
  score_evergreen_value: 0.7,
  score_audience_fit: 0.5,
  score_content_novelty: 0.9,
  status_trend_strength: 'absent',
  status_audience_demand: 'present',
  status_competition: 'present',
  status_evergreen_value: 'present',
  status_audience_fit: 'present',
  status_content_novelty: 'present',
}

export const opportunityEvidenceResponse: OpportunityEvidenceResponse = {
  opportunity_id: 1,
  evidence_count: 3,
  snapshots: [
    {
      source_label: 'market_intelligence:canonical=4:snap=34',
      collected_at: '2026-08-29T00:11:55',
      items: [
        { evidence_type: 'market_demand_score', label: 'Audience demand', value: 0.62, text: null, unit: 'score [0,1]' },
        { evidence_type: 'market_maturity', label: 'Evidence maturity', value: null, text: 'actionable', unit: 'maturity_level' },
        { evidence_type: 'market_signal_snapshot_id', label: 'Signal snapshot', value: 34, text: null, unit: 'id' },
      ],
    },
  ],
}

export const marketRefreshSchedule: ScheduleView = {
  id: 'sched-market-refresh-001',
  workspace_id: WS_ID,
  channel_id: CH_ID,
  name: 'market-refresh:orvella',
  operation_type: 'market_refresh',
  schedule_type: 'interval',
  schedule_config: { interval_seconds: 21600 },
  timezone: 'UTC',
  is_active: true,
  last_run_at: '2026-08-29T00:11:55',
  next_run_at: '2026-08-29T06:11:55',
  actor: 'dev:studio-user',
  created_at: '2026-08-29T00:00:00',
  updated_at: '2026-08-29T00:11:55',
}

export const marketExperiment: MarketExperiment = {
  id: 'exp-fixture-001',
  channel_id: 1,
  opportunity_id: 1,
  experiment_type: 'exploration',
  hypothesis: "Publishing on 'crispr gene editing technology' will produce measurable signal on average_view_percentage.",
  status: 'draft',
  created_at: '2026-08-24T04:02:24',
}

export const experimentStrategyBrief: ExperimentStrategyBrief = {
  id: 'brief-fixture-001',
  channel_id: 1,
  planning_run_id: 'plan-fixture-001',
  selection_decision_id: 1,
  opportunity_id: 1,
  canonical_cluster_id: 4,
  channel_profile_version_id: 1,
  brief_planning_intent: 'market_exploration',
  experiment_type: 'exploration',
  market_theme: 'crispr gene editing technology',
  canonical_topic: 'crispr gene editing technology',
  strategic_reason: "No prior experiments on cluster 'crispr gene editing technology'; baseline signal needed.",
  information_gain_reason: 'Untested cluster: high cluster coverage gain, no feature confound.',
  hypothesis: "Publishing on 'crispr gene editing technology' will produce measurable signal on average_view_percentage.",
  target_metric: 'average_view_percentage',
  target_direction: 'higher_is_better',
  treatment_factors: [],
  controlled_factors: [
    { factor_name: 'narration_speaking_rate', baseline_value: null, baseline_source: 'voice_profile', tolerance: '±0.80' },
  ],
  content_constraints: {
    excluded_topics: ['gambling'],
    primary_niche: 'science and technology explained',
    secondary_niches: [],
    brand_voice: 'conversational',
    content_style: 'explainer',
    audience_description: 'Curious adults and young learners.',
  },
  confounding_risk: 'low',
  policy_version: '1.0',
  eligibility_classification: 'eligible',
  score_decomposition_json: '{}',
  brief_hash: 'hash-brief-fixture',
  status: 'pending_approval',
  created_at: '2026-08-24T04:02:24',
  linked_experiment: {
    id: marketExperiment.id,
    status: marketExperiment.status,
    experiment_type: marketExperiment.experiment_type,
    created_at: marketExperiment.created_at,
  },
}

export const channelPerformanceBaseline: ChannelPerformanceBaseline = {
  id: 1,
  channel_id: CH_ID,
  workspace_id: WS_ID,
  metric_name: 'views',
  period_type: 'lifetime',
  publication_count: 2,
  mean: 150,
  median: 150,
  min_value: 19,
  max_value: 474,
  std_dev: 200,
  sample_maturity: 'exploratory',
  source_publication_ids_json: '[1,3]',
  source_snapshot_ids_json: '[1,2]',
  comparison_schema_version: 'cross-pub-v1',
  observer_version: 'observer-v1',
  input_hash: 'hash-baseline-fixture',
  created_at: '2026-08-28T00:00:00',
  updated_at: '2026-08-28T00:00:00',
}

export const featurePerformanceObservation: FeaturePerformanceObservation = {
  id: 1,
  channel_id: CH_ID,
  workspace_id: WS_ID,
  feature_name: 'scene_count',
  feature_bucket: '6–9',
  metric_name: 'views',
  period_type: 'lifetime',
  publication_count: 2,
  mean: 246.5,
  median: 246.5,
  min_value: 19,
  max_value: 474,
  std_dev: 200,
  baseline_mean: 150,
  baseline_median: 150,
  abs_diff_from_baseline: 96.5,
  rel_diff_from_baseline: 0.64,
  sample_maturity: 'exploratory',
  observation_type: 'association',
  source_publication_ids_json: '[1,3]',
  source_snapshot_ids_json: '[1,2]',
  comparison_schema_version: 'cross-pub-v1',
  observer_version: 'observer-v1',
  input_hash: 'hash-observation-fixture',
  created_at: '2026-08-28T00:00:00',
  updated_at: '2026-08-28T00:00:00',
}

export const strategyConfig: StrategyConfig = {
  schema_version: '1.0',
  bootstrap: {
    target_publication_count: 18,
    market_intelligence_weight: 0.8,
    channel_evidence_weight: 0.2,
    exploration_share: 0.67,
  },
  steady_state: {
    market_intelligence_weight: 0.4,
    channel_evidence_weight: 0.6,
    exploration_share: 0.2,
  },
  transition: {
    trigger_metric: 'average_view_percentage',
    maturity_threshold: 'directional',
  },
  diversity: {
    max_cluster_share: 0.4,
    max_consecutive_same_cluster: 2,
  },
  creative_dimensions: ['topic_theme', 'hook', 'pacing', 'duration', 'structure', 'caption_density', 'publish_timing'],
  total_portfolio_slots: 3,
}

export const strategyProfile: StrategyProfile = {
  id: 'strat-fixture-001',
  channel_id: CH_ID,
  version: 1,
  config_json: JSON.stringify(strategyConfig),
  actor: 'dev:studio-user',
  created_at: '2026-08-28T00:00:00',
  is_active: true,
}

export const channelStrategyResponse: ChannelStrategyResponse = {
  status: 'ok',
  profile: strategyProfile,
  effective: {
    trigger_metric: 'average_view_percentage',
    maturity_threshold: 'directional',
    current_maturity: 'insufficient',
    publication_count: 0,
    effective_regime: 'bootstrap',
    market_intelligence_weight: 0.8,
    channel_evidence_weight: 0.2,
    exploration_share: 0.67,
  },
  config_errors: null,
}

export const autonomyReadinessResponse: AutonomyReadinessResponse = {
  channel_id: CH_ID,
  checks: [
    { key: 'market_intelligence_configured', label: 'Market intelligence configured', ready: true, status: 'ready', category: 'decision', detail: 'YouTube Data API key configured (ACE_YOUTUBE_API_KEY)' },
    { key: 'recurring_market_refresh', label: 'Recurring market refresh active', ready: true, status: 'ready', category: 'decision', detail: 'Recurring market_refresh schedule active (next run 2026-08-29T06:15:14+00:00)' },
    { key: 'strategy_profile_active', label: 'Strategy profile active', ready: true, status: 'ready', category: 'decision', detail: 'Active strategy profile v1' },
    { key: 'eligible_opportunities_available', label: 'Eligible opportunities available', ready: true, status: 'ready', category: 'decision', detail: '2 of 8 checked opportunities are eligible' },
    { key: 'decision_automation_enabled', label: 'Decision automation enabled', ready: true, status: 'ready', category: 'decision', detail: 'Enabled — cadence daily, queue target 1, America/New_York' },
    { key: 'production_automation_enabled', label: 'Production automation enabled', ready: true, status: 'ready', category: 'production', detail: 'Enabled — filled slots are produced without per-video approval' },
    { key: 'production_queue_healthy', label: 'Production queue healthy', ready: true, status: 'ready', category: 'production', detail: '0 of 1 queue slot(s) filled, 0 reserved' },
    { key: 'analytics_observer_active', label: 'Analytics observer active', ready: true, status: 'ready', category: 'analytics_learning', detail: '3 publication(s) under active analytics observation' },
    { key: 'channel_learning_evidence', label: 'Channel learning evidence', ready: false, status: 'degraded', category: 'analytics_learning', detail: "10 baseline metric(s) over 1 publication(s); maturity 'insufficient'" },
    { key: 'experiment_ledger_current', label: 'Experiment ledger current', ready: true, status: 'ready', category: 'analytics_learning', detail: "Every public publication's experiment has advanced past production" },
    { key: 'provider_account_healthy', label: 'Provider account healthy', ready: true, status: 'ready', category: 'provider_oauth', detail: "Connected YouTube account (status 'connected')" },
    { key: 'release_scope_granted', label: 'Public-release OAuth scope granted', ready: true, status: 'ready', category: 'provider_oauth', detail: 'youtube.force-ssl granted — the credential can make a video public' },
    { key: 'public_publishing_authorized', label: 'Autonomous public publishing authorized', ready: false, status: 'blocked', category: 'publishing_authorization', detail: 'Blocked by: global_publishing_gate_off, global_release_gate_off, channel_not_authorized' },
    { key: 'global_publishing_gates', label: 'Global publishing gates', ready: true, status: 'ready', category: 'publishing_authorization', detail: 'Both global gates OFF — no process can publish or release' },
    { key: 'autonomy_schedules_healthy', label: 'Autonomy schedules healthy', ready: true, status: 'ready', category: 'scheduler', detail: 'All 4 autonomy schedules active and on time' },
  ],
  ready_for_decision_automation: true,
  authorized_for_public_publishing: false,
  categories: [
    { key: 'decision', label: 'Decision readiness', status: 'ready', check_keys: ['market_intelligence_configured', 'recurring_market_refresh', 'strategy_profile_active', 'eligible_opportunities_available', 'decision_automation_enabled'] },
    { key: 'production', label: 'Production readiness', status: 'ready', check_keys: ['production_automation_enabled', 'production_queue_healthy'] },
    { key: 'analytics_learning', label: 'Analytics & learning readiness', status: 'degraded', check_keys: ['analytics_observer_active', 'channel_learning_evidence', 'experiment_ledger_current'] },
    { key: 'provider_oauth', label: 'OAuth / provider readiness', status: 'ready', check_keys: ['provider_account_healthy', 'release_scope_granted'] },
    { key: 'publishing_authorization', label: 'Autonomous public publishing', status: 'blocked', check_keys: ['public_publishing_authorized', 'global_publishing_gates'] },
    { key: 'scheduler', label: 'Scheduler health', status: 'ready', check_keys: ['autonomy_schedules_healthy'] },
  ],
  overall_status: 'blocked',
}

export const autonomyPolicyEnabled: AutonomyPolicy = {
  channel_id: CH_ID,
  workspace_id: WS_ID,
  decision_automation_enabled: true,
  production_automation_enabled: true,
  cadence_type: 'daily',
  cadence_interval_days: null,
  cadence_cron: null,
  preferred_local_hour: 9,
  timezone: 'America/New_York',
  queue_target: 1,
  market_refresh_max_age_hours: 12,
  semantic_fit_max_evaluations_per_run: 5,
  last_decision_at: '2026-08-29T09:00:00',
  last_decision_outcome: 'selected',
  actor: 'dev:studio-user',
  created_at: '2026-08-28T00:00:00',
  updated_at: '2026-08-29T09:00:00',
}

export const publishingSlotFilled: PublishingSlot = {
  id: 1,
  channel_id: CH_ID,
  workspace_id: WS_ID,
  slot_key: '2026-08-30',
  scheduled_for_local: '2026-08-30T09:00:00-04:00',
  timezone: 'America/New_York',
  scheduled_for_utc: '2026-08-30T13:00:00',
  state: 'filled',
  brief_id: 'brief-fixture-001',
  selection_decision_id: 1,
  opportunity_id: 7,
  reserved_at: '2026-08-29T09:00:00',
  filled_at: '2026-08-29T09:01:00',
  cancelled_at: null,
  cancellation_reason: null,
  experiment_id: 'exp-slot-1',
  production_status: 'ready',
  production_pipeline_id: 'pipe-fixture-001',
  production_publishing_plan_id: 1,
  production_started_at: '2026-08-29T09:02:00',
  production_ready_at: '2026-08-29T09:20:00',
  production_failed_at: null,
  production_failed_stage: null,
  production_error: null,
  production_retry_count: 0,
  publish_status: null,
  publication_id: null,
  publish_provider_video_id: null,
  publish_started_at: null,
  publish_uploaded_at: null,
  publish_released_at: null,
  publish_failed_at: null,
  publish_failure_category: null,
  publish_error: null,
  publish_retry_count: 0,
  rescheduled_from_slot_id: null,
}

export const publishingAuthorizationUnauthorized: ChannelPublishingAuthorizationResponse = {
  authorization: null,
  decision: {
    allowed: false,
    blocked_by: [
      'global_publishing_gate_off', 'global_release_gate_off',
      'channel_not_authorized', 'release_scope_missing',
    ],
    detail:
      'Blocked by: global_publishing_gate_off, global_release_gate_off, '
      + 'channel_not_authorized, release_scope_missing',
    global_publishing_enabled: false,
    global_release_enabled: false,
    channel_authorized: false,
    publications_last_24h: 0,
    max_publications_per_24h: 1,
    account_id: 'acct-fixture-001',
    account_status: 'connected',
    release_scope_granted: false,
  },
}

export const publishingAuthorizationAuthorized: ChannelPublishingAuthorizationResponse = {
  authorization: {
    channel_id: CH_ID,
    workspace_id: WS_ID,
    authorized: true,
    authorized_at: '2026-08-29T10:00:00',
    authorized_by: 'dev:studio-user',
    revoked_at: null,
    revoked_by: null,
    policy_version: 1,
    max_publications_per_24h: 1,
    missed_slot_grace_minutes: 120,
    created_at: '2026-08-29T10:00:00',
    updated_at: '2026-08-29T10:00:00',
  },
  decision: {
    allowed: true,
    blocked_by: [],
    detail: 'All publishing authorization layers passed.',
    global_publishing_enabled: true,
    global_release_enabled: true,
    channel_authorized: true,
    publications_last_24h: 0,
    max_publications_per_24h: 1,
    account_id: 'acct-fixture-001',
    account_status: 'connected',
    release_scope_granted: true,
  },
}

export const channelAutomationPolicyResponse: ChannelAutomationPolicyResponse = {
  policy: autonomyPolicyEnabled,
  active_slots: [publishingSlotFilled],
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
  render_duration_ms: 58607,
  topic_title: 'Renewable Energy',
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
  topic_title: 'Renewable Energy',
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
  experiment_id: null,
}

export const publicationAnalyticsHistory: PublicationAnalyticsHistoryEntry[] = [
  {
    snapshot_id: 1,
    ingested_at: '2026-08-18T01:19:39',
    observed_at: '2026-08-18T01:19:39',
    period_start: '2026-08-10',
    period_end: '2026-08-17',
    observation_state: 'data',
    experiment_id: null,
    metrics: { views: 0, watch_time_seconds: 0, average_view_percentage: 0 },
  },
  {
    snapshot_id: 2,
    ingested_at: '2026-08-27T01:19:39',
    observed_at: '2026-08-27T01:19:39',
    period_start: '2026-08-10',
    period_end: '2026-08-26',
    observation_state: 'data',
    experiment_id: null,
    metrics: { views: 1234, watch_time_seconds: 10380, average_view_percentage: 95.57 },
  },
]

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

// ── Phase 18E — visual quality ──────────────────────────────────────────────

export const publicationVisualQuality = {
  assessed: true,
  status: 'blocked' as const,
  assessment_version: 'visual-assessment-v1',
  policy_version: 'visual-quality-policy-v1',
  visual_style: 'balanced',
  total_beat_count: 18,
  total_duration_ms: 69_474,
  scene_count: 5,
  meaningful_runtime_pct: 0.162,
  text_card_runtime_pct: 0.838,
  meaningful_beat_count: 3,
  visual_changes_per_minute: 13.8,
  distinct_asset_count: 16,
  asset_reuse_ratio: 0.11,
  max_meaningful_gap_ms: 50_300,
  avg_meaningful_gap_ms: 29_100,
  opening_meaningful_visual: true,
  dominant_family: 'text_card',
  dominant_family_share: 0.838,
  family_diversity: 0.64,
  family_distribution: [
    { family: 'text_card', beat_count: 15, runtime_ms: 58_200, runtime_pct: 0.838 },
    { family: 'generated_diagram', beat_count: 3, runtime_ms: 11_274, runtime_pct: 0.162 },
  ],
  fallback_beat_count: 18,
  provider_fallback_beats: 15,
  creative_fallback_beats: 3,
  provider_fallback_rate: 0.833,
  fallback_reasons: { all_candidates_rejected: 15, structural_intent_prefers_graphic: 3 },
  planned_meaningful_beats: 14,
  remediation_attempts: 0,
  remediated: false,
  findings: [
    {
      code: 'visual_meaningful_runtime_below_floor',
      severity: 'blocking' as const,
      message:
        'Only 16% of runtime carries a meaningful visual (floor 25%); 84% is text-card runtime.',
      evidence: { meaningful_runtime_pct: 0.162 },
    },
    {
      code: 'visual_text_card_heavy',
      severity: 'warning' as const,
      message: '84% of runtime is text cards.',
      evidence: {},
    },
  ],
  scene_diagnostics: [
    {
      beat_index: 0, scene_index: 0, start_ms: 0, duration_ms: 3_800,
      visual_intent: 'timeline', planned: 'generated_diagram',
      realized: 'generated_diagram', meaningful: true, provider: 'programmatic',
      fallback_reason: 'structural_intent_prefers_graphic',
      fallback_class: 'creative' as const,
    },
    {
      beat_index: 1, scene_index: 0, start_ms: 3_800, duration_ms: 4_100,
      visual_intent: 'action', planned: 'motion_footage',
      realized: 'text_card', meaningful: false, provider: 'programmatic',
      fallback_reason: 'all_candidates_rejected',
      fallback_class: 'provider' as const,
    },
  ],
  assessed_at: '2026-08-30T12:00:00',
}

export const publicationVisualQualityUnassessed = { assessed: false }
