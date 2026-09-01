/* Product-facing labels and defaults for the Channel Strategy Profile
   (Phase 17E). Mirrors app.intelligence.experiments.strategy_policy on the
   backend — the default config and validation rules must stay in sync with
   that module, since the backend is the source of truth (this file's
   client-side validation is a UX convenience, not the authority).

   No topic names appear anywhere here. The strategy expresses POLICY
   (how much weight market intelligence vs. the channel's own evidence gets,
   how diverse candidates must be, how many publications to explore before
   leaning on first-party evidence) — never a list of topics to chase. */

import type { StrategyConfig } from '@/api/types'
import { formatPercent } from '@/lib/format'

export const STRATEGY_SCHEMA_VERSION = '1.0'

export function defaultBootstrapStrategyConfig(): StrategyConfig {
  return {
    schema_version: STRATEGY_SCHEMA_VERSION,
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
    creative_dimensions: [
      'topic_theme', 'hook', 'pacing', 'duration', 'structure', 'caption_density', 'publish_timing',
    ],
    total_portfolio_slots: 3,
  }
}

/** Client-side sanity check mirroring the backend's validate_strategy_config.
    The backend remains authoritative — this only avoids a round-trip for
    obviously-invalid input in the edit form. */
export function validateStrategyConfig(config: StrategyConfig): string[] {
  const errors: string[] = []
  const checkRegime = (name: 'bootstrap' | 'steady_state') => {
    const r = config[name]
    if (!r) { errors.push(`'${name}' is required`); return }
    for (const key of ['market_intelligence_weight', 'channel_evidence_weight', 'exploration_share'] as const) {
      const v = r[key]
      if (typeof v !== 'number' || v < 0 || v > 1) errors.push(`'${name}.${key}' must be between 0 and 1`)
    }
  }
  checkRegime('bootstrap')
  checkRegime('steady_state')
  if (!config.bootstrap?.target_publication_count || config.bootstrap.target_publication_count < 1) {
    errors.push("'bootstrap.target_publication_count' must be a positive integer")
  }
  if (!(MATURITY_LEVELS as readonly string[]).includes(config.transition?.maturity_threshold)) {
    errors.push(`'transition.maturity_threshold' must be one of ${MATURITY_LEVELS.join(', ')}`)
  }
  const share = config.diversity?.max_cluster_share
  if (typeof share !== 'number' || share <= 0 || share > 1) {
    errors.push("'diversity.max_cluster_share' must be between 0 (exclusive) and 1")
  }
  if (!config.diversity?.max_consecutive_same_cluster || config.diversity.max_consecutive_same_cluster < 1) {
    errors.push("'diversity.max_consecutive_same_cluster' must be a positive integer")
  }
  if (!config.total_portfolio_slots || config.total_portfolio_slots < 1) {
    errors.push("'total_portfolio_slots' must be a positive integer")
  }
  return errors
}

export const MATURITY_LEVELS = ['insufficient', 'exploratory', 'directional', 'actionable'] as const

const MATURITY_LABEL: Record<string, string> = {
  insufficient: 'Not enough evidence',
  exploratory: 'Exploratory',
  directional: 'Directional',
  actionable: 'Actionable',
}

export function maturityLabel(level: string): string {
  return MATURITY_LABEL[level] ?? level
}

const REGIME_LABEL: Record<string, string> = {
  bootstrap: 'Bootstrap exploration',
  steady_state: 'Steady state',
}

export function regimeLabel(regime: string): string {
  return REGIME_LABEL[regime] ?? regime
}

const DIMENSION_LABEL: Record<string, string> = {
  topic_theme: 'Topic / theme',
  hook: 'Hook',
  pacing: 'Pacing',
  duration: 'Duration',
  structure: 'Structure',
  caption_density: 'Caption density',
  publish_timing: 'Publish timing',
}

export function creativeDimensionLabel(dim: string): string {
  return DIMENSION_LABEL[dim] ?? dim
}

/** e.g. "80% market intelligence / 20% channel evidence" */
export function weightSplitLabel(marketWeight: number | null, channelWeight: number | null): string {
  if (marketWeight === null || channelWeight === null) return '—'
  const total = marketWeight + channelWeight
  if (total <= 0) return '—'
  return `${formatPercent((marketWeight / total) * 100, 0)} market intelligence / ${formatPercent((channelWeight / total) * 100, 0)} channel evidence`
}
