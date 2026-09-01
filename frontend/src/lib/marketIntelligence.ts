/* Product-facing labels and formatting for market intelligence and
   cross-publication creative-factor data (Phase 17D).

   Two honesty rules baked in throughout this file:
   1. A null/absent score is never rendered as 0 or omitted silently — it
      must read as "not available yet", because the backend distinguishes
      "no evidence" from "evidence of zero" and the UI must preserve that.
   2. score_competition is INVERTED in the backend: a HIGH score means LOW
      competition (more attractive to enter). Every place that surfaces
      "competition" to a user must apply this inversion — see
      competitionLabel() below. Getting this backwards would tell an
      operator the opposite of the truth.
*/

import { formatPercent } from '@/lib/format'

// ── Opportunity scores ──────────────────────────────────────────────────────

export type FactorStatus = 'present' | 'absent' | 'insufficient' | 'degraded' | string

const FACTOR_STATUS_LABEL: Record<string, string> = {
  present: 'Available',
  absent: 'Not available yet',
  insufficient: 'Not enough evidence',
  degraded: 'Evidence is stale',
}

export function factorStatusLabel(status: FactorStatus | null | undefined): string {
  if (!status) return 'Not available yet'
  return FACTOR_STATUS_LABEL[status] ?? status
}

/** A [0,1] opportunity sub-score as a percentage, or a clear "not available"
    label when the score is null (which the backend uses for absent
    evidence — never fabricate a value in that case). */
export function scoreLabel(value: number | null | undefined, status?: FactorStatus): string {
  if (value === null || value === undefined) return factorStatusLabel(status)
  return formatPercent(value * 100, 0)
}

/** score_competition is inverted: high score = low competition. Convert to
    the plain-language claim an operator actually wants — "how much
    competition is there for this topic" — rather than exposing the raw
    (and easily misread) score directly. */
export function competitionLabel(value: number | null | undefined, status?: FactorStatus): string {
  if (value === null || value === undefined) return factorStatusLabel(status)
  if (value >= 0.66) return 'Low competition'
  if (value >= 0.33) return 'Moderate competition'
  return 'High competition'
}

export const OPPORTUNITY_FACTORS: {
  key:
    | 'score_trend_strength'
    | 'score_audience_demand'
    | 'score_competition'
    | 'score_evergreen_value'
    | 'score_audience_fit'
    | 'score_content_novelty'
  statusKey:
    | 'status_trend_strength'
    | 'status_audience_demand'
    | 'status_competition'
    | 'status_evergreen_value'
    | 'status_audience_fit'
    | 'status_content_novelty'
  label: string
  hint: string
}[] = [
  {
    key: 'score_trend_strength',
    statusKey: 'status_trend_strength',
    label: 'Momentum',
    hint: 'How fast interest in this topic is rising right now.',
  },
  {
    key: 'score_audience_demand',
    statusKey: 'status_audience_demand',
    label: 'Audience demand',
    hint: 'How much viewer attention this topic attracts on YouTube.',
  },
  {
    key: 'score_competition',
    statusKey: 'status_competition',
    label: 'Competition',
    hint: 'How crowded this topic is with other creators — higher score means less competition.',
  },
  {
    key: 'score_evergreen_value',
    statusKey: 'status_evergreen_value',
    label: 'Evergreen value',
    hint: 'Whether this topic stays relevant over time, or is tied to a passing moment.',
  },
  {
    key: 'score_audience_fit',
    statusKey: 'status_audience_fit',
    label: 'Audience fit',
    hint: "How well this topic matches Orvella's own niche and audience.",
  },
  {
    key: 'score_content_novelty',
    statusKey: 'status_content_novelty',
    label: 'Novelty',
    hint: "How different this is from ideas Orvella has already explored.",
  },
]

const LIFECYCLE_LABEL: Record<string, string> = {
  new: 'New',
  under_review: 'Under review',
  approved: 'Approved',
  rejected: 'Rejected',
  produced: 'Produced',
  archived: 'Archived',
}

export function lifecycleLabel(state: string): string {
  return LIFECYCLE_LABEL[state] ?? state
}

const STRATEGIC_ROLE_LABEL: Record<string, string> = {
  discovery: 'Discovery',
  monetization: 'Monetization',
  subscriber_growth: 'Subscriber growth',
  authority: 'Authority building',
  retention: 'Retention',
  experimentation: 'Experimentation',
}

export function strategicRoleLabel(role: string): string {
  return STRATEGIC_ROLE_LABEL[role] ?? role
}

// ── Experiments & strategy briefs ───────────────────────────────────────────

const EXPERIMENT_STATUS_LABEL: Record<string, string> = {
  draft: 'Draft',
  planned: 'Planned',
  in_production: 'In production',
  published: 'Published',
  observing: 'Observing',
  mature: 'Mature',
  analyzed: 'Analyzed',
  completed: 'Completed',
  cancelled: 'Cancelled',
}

export function experimentStatusLabel(status: string): string {
  return EXPERIMENT_STATUS_LABEL[status] ?? status
}

const BRIEF_STATUS_LABEL: Record<string, string> = {
  pending_approval: 'Awaiting review',
  approved: 'Approved',
  superseded: 'Superseded',
}

export function briefStatusLabel(status: string): string {
  return BRIEF_STATUS_LABEL[status] ?? status
}

const CONFOUNDING_RISK_TONE: Record<string, 'healthy' | 'warn' | 'error'> = {
  low: 'healthy',
  moderate: 'warn',
  high: 'error',
}

export function confoundingRiskTone(risk: string): 'healthy' | 'warn' | 'error' {
  return CONFOUNDING_RISK_TONE[risk.toLowerCase()] ?? 'warn'
}

export function targetDirectionLabel(direction: string): string {
  return direction === 'higher_is_better' ? 'higher is better' : 'lower is better'
}

// ── Creative factors (cross-publication learning) ───────────────────────────
// Mirrors ALL_COMPARABLE_FEATURES in app.learning.cross_publication exactly —
// do not add factors the backend does not track.

export type FeatureGroup = 'Script' | 'Narration' | 'Production' | 'Publishing'

export const CREATIVE_FACTORS: { key: string; label: string; group: FeatureGroup }[] = [
  { key: 'script_word_count', label: 'Script length (words)', group: 'Script' },
  { key: 'hook_word_count', label: 'Hook length (words)', group: 'Script' },
  { key: 'has_hook', label: 'Has a hook', group: 'Script' },
  { key: 'has_cta', label: 'Has a call to action', group: 'Script' },
  { key: 'script_format', label: 'Script format', group: 'Script' },
  { key: 'narration_speaking_rate', label: 'Speaking rate', group: 'Narration' },
  { key: 'words_per_second', label: 'Words per second', group: 'Narration' },
  { key: 'narration_actual_duration_s', label: 'Video duration', group: 'Narration' },
  { key: 'narration_provider', label: 'Narration voice provider', group: 'Narration' },
  { key: 'narration_voice_id', label: 'Narration voice', group: 'Narration' },
  { key: 'narration_language', label: 'Narration language', group: 'Narration' },
  { key: 'scene_count', label: 'Scene count', group: 'Production' },
  { key: 'scenes_per_minute', label: 'Scene pacing', group: 'Production' },
  { key: 'avg_scene_duration_ms', label: 'Average scene length', group: 'Production' },
  { key: 'scene_dominant_shot_type', label: 'Dominant shot type', group: 'Production' },
  { key: 'scene_dominant_transition', label: 'Dominant transition', group: 'Production' },
  { key: 'scene_has_ai_generated_assets', label: 'Uses AI-generated visuals', group: 'Production' },
  { key: 'caption_cues_per_second', label: 'Caption density', group: 'Production' },
  { key: 'render_caption_burn_in', label: 'Captions burned in', group: 'Production' },
  { key: 'publish_hour_utc', label: 'Publish hour (UTC)', group: 'Publishing' },
  { key: 'publish_day_of_week', label: 'Publish day of week', group: 'Publishing' },
  { key: 'publish_visibility', label: 'Publish visibility', group: 'Publishing' },
  { key: 'publish_made_for_kids', label: "Marked 'made for kids'", group: 'Publishing' },
  { key: 'learning_application_used', label: 'Applied a prior learning', group: 'Publishing' },
]

export function creativeFactorLabel(featureName: string): string {
  return CREATIVE_FACTORS.find(f => f.key === featureName)?.label ?? featureName
}

const OUTCOME_METRIC_LABEL: Record<string, string> = {
  views: 'Views',
  engaged_views: 'Engaged views',
  watch_time_seconds: 'Watch time',
  average_view_duration: 'Avg view duration',
  average_view_percentage: 'Avg % viewed',
  likes: 'Likes',
  dislikes: 'Dislikes',
  comments: 'Comments',
  shares: 'Shares',
  subscribers_gained: 'Subscribers gained',
  subscribers_lost: 'Subscribers lost',
}

export function outcomeMetricLabel(metricName: string): string {
  return OUTCOME_METRIC_LABEL[metricName] ?? metricName
}
