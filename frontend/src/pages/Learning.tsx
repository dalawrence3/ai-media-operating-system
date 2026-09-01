/* Phase 17D — Learn: decision-support layer for the channel.

   Answers "what are we learning, what appears to be working, what
   opportunities exist on YouTube right now, and what should we test next?"
   Distinct from Analytics ("what happened?") and never blurs:
     - observed channel performance (real, first-party)
     - correlations/associations (cross-publication learning — observational)
     - hypotheses (strategy briefs — proposed, not run)
     - recommendations (optimization_recommendations — human-reviewed)
     - external market signals (opportunities — YouTube at large, not Orvella)

   CRITICAL SEMANTICS (carried forward from Phase 11/14/17D audits):
   - confidence = heuristic signal strength, NOT statistical confidence interval
   - observational recommendation ≠ causal proof — never say "causes"
   - Two maturity vocabularies exist and are deliberately not merged:
       sample_maturity (cross-publication): insufficient/exploratory/directional/actionable
       recommendation_strength (optimization_recommendations): exploratory/actionable,
         paired with a separate confidence: low/medium/high
   - score_competition is inverted (high score = low competition) — see
     lib/marketIntelligence.ts, which every render of it must go through.
   - Accepting a recommendation does NOT auto-apply it. Strategy briefs are
     proposals awaiting human review, never auto-scheduled or auto-produced.
*/

import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { PageHeader } from '@/components/common/PageHeader'
import { SectionHeader } from '@/components/common/SectionHeader'
import { MetricCard } from '@/components/common/MetricCard'
import { MaturityBadge } from '@/components/common/MaturityBadge'
import { OpportunityCard } from '@/components/common/OpportunityCard'
import { StrategyBriefCard } from '@/components/common/StrategyBriefCard'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadingState } from '@/components/common/LoadingState'
import { UnavailableState } from '@/components/common/UnavailableState'
import { useCurrentChannel } from '@/hooks/useCurrentChannel'
import { useChannelPerformance } from '@/hooks/useChannelPerformance'
import {
  useChannelRecommendations,
  useAcceptRecommendation,
  useRejectRecommendation,
  useCrossPublicationLearning,
  useMarketOpportunities,
  useMarketExperiments,
  useStrategyBriefs,
  useMarketRefreshSchedule,
} from '@/hooks/useLearning'
import { LocalTime } from '@/components/common/LocalTime'
import { formatCompact, formatPercent, formatWatchTime } from '@/lib/format'
import {
  creativeFactorLabel,
  outcomeMetricLabel,
  CREATIVE_FACTORS,
  type FeatureGroup,
} from '@/lib/marketIntelligence'
import type { OptimizationRecommendation, FeaturePerformanceObservation } from '@/api/types'

const STATUS_FILTERS = ['all', 'pending', 'accepted', 'rejected'] as const
type StatusFilter = (typeof STATUS_FILTERS)[number]

const FEATURE_GROUPS: FeatureGroup[] = ['Script', 'Narration', 'Production', 'Publishing']

const CONFIDENCE_MODEL = [
  {
    label: 'Confidence Score',
    desc: 'A heuristic signal strength (0–1) — NOT a statistical confidence interval. Combines volume (log₂ snapshot count), effect size (gap/threshold ratio), and consistency (period diversity).',
  },
  {
    label: 'Exploratory',
    desc: 'Insufficient evidence to act. Confidence below threshold or fewer than 2 unique snapshot IDs. Worth monitoring.',
  },
  {
    label: 'Actionable',
    desc: 'Confidence ≥ 0.40 AND ≥ 2 unique snapshot IDs. Sufficient evidence to consider applying — but accepting a recommendation does not auto-apply it.',
  },
  {
    label: 'Evidence Classification',
    desc: 'All recommendations are observational. A recommendation associated with an experiment ID does NOT qualify as controlled-experiment evidence. They describe associations, not causes.',
  },
  {
    label: 'Accept / Reject',
    desc: 'Human review of recommendations only. Accepting marks the recommendation as accepted — it does not modify any upstream engine, prompt, or configuration.',
  },
  {
    label: 'Cross-publication maturity',
    desc: 'A separate scale from recommendation confidence — insufficient (n<2) → exploratory (2–3) → directional (4–9) → actionable (≥10 publications). Scores how much cross-video evidence a creative-factor pattern rests on.',
  },
] as const

function confidenceColor(confidence: string): string {
  switch (confidence) {
    case 'high':   return 'var(--status-ok)'
    case 'medium': return 'var(--status-warn)'
    default:       return 'var(--status-secondary)'
  }
}

function strengthBadge(strength: string) {
  const isActionable = strength === 'actionable'
  return (
    <span
      className="tag"
      style={{
        background: isActionable ? 'var(--status-info-bg)' : 'var(--surface-secondary)',
        color:      isActionable ? 'var(--status-info)' : 'var(--text-secondary)',
      }}
    >
      {strength}
    </span>
  )
}

function RecommendationCard({
  rec,
  workspaceId,
}: {
  rec: OptimizationRecommendation
  workspaceId: string
}) {
  const [showReject, setShowReject] = useState(false)
  const [rejectNotes, setRejectNotes] = useState('')
  const accept = useAcceptRecommendation(workspaceId)
  const reject = useRejectRecommendation(workspaceId)

  const isPending   = rec.status === 'pending'
  const isAccepted  = rec.status === 'accepted'
  const isRejected  = rec.status === 'rejected'
  const isSuperseded = rec.status === 'superseded'

  function handleAccept() {
    accept.mutate({ id: rec.id })
  }

  function handleRejectConfirm() {
    if (!rejectNotes.trim()) return
    reject.mutate(
      { id: rec.id, notes: rejectNotes.trim() },
      {
        onSuccess: () => {
          setShowReject(false)
          setRejectNotes('')
        },
      },
    )
  }

  return (
    <div className="card" data-testid={`recommendation-${rec.id}`}>
      <div className="flex items-start justify-between gap-4 mb-3">
        <div>
          <div className="flex items-center gap-2 mb-1">
            {strengthBadge(rec.recommendation_strength)}
            <span className="tag" style={{ background: 'var(--surface-secondary)', color: 'var(--text-secondary)' }}>
              {rec.domain}
            </span>
            {isAccepted   && <span className="tag" style={{ background: 'var(--status-ok-bg)', color: 'var(--status-ok)' }}>accepted</span>}
            {isRejected   && <span className="tag" style={{ background: 'var(--status-error-bg)', color: 'var(--status-error)' }}>rejected</span>}
            {isSuperseded && <span className="tag" style={{ background: 'var(--surface-secondary)', color: 'var(--text-secondary)' }}>superseded</span>}
          </div>
          <h3 className="text-sm font-medium">{rec.title}</h3>
        </div>
        <div className="text-right" style={{ flexShrink: 0 }}>
          <div
            className="text-sm font-mono font-medium"
            style={{ color: confidenceColor(rec.confidence) }}
          >
            {(rec.confidence_score * 100).toFixed(0)}%
          </div>
          <div className="text-xs text-secondary">{rec.confidence} confidence</div>
        </div>
      </div>

      <p className="text-sm text-secondary mb-2">{rec.explanation}</p>
      <p className="text-sm mb-3">
        <span className="text-secondary">Associated with: </span>
        {rec.expected_improvement}
      </p>

      <div className="text-xs text-secondary mb-3">
        {rec.subsystem} · {rec.measure}
      </div>

      {isPending && !showReject && (
        <div className="flex gap-2">
          <button
            className="button button-sm button-primary"
            onClick={handleAccept}
            disabled={accept.isPending}
            data-testid={`accept-${rec.id}`}
          >
            {accept.isPending ? 'Accepting…' : 'Accept'}
          </button>
          <button
            className="button button-sm button-secondary"
            onClick={() => setShowReject(true)}
            data-testid={`reject-open-${rec.id}`}
          >
            Reject
          </button>
        </div>
      )}

      {accept.isError && (
        <p className="text-sm mt-2" style={{ color: 'var(--status-error)' }}>
          {(accept.error as Error).message}
        </p>
      )}

      {isPending && showReject && (
        <div className="mt-2">
          <textarea
            className="input input-sm w-full mb-2"
            rows={3}
            placeholder="Rejection reason (required)…"
            value={rejectNotes}
            onChange={e => setRejectNotes(e.target.value)}
            data-testid={`reject-notes-${rec.id}`}
          />
          <div className="flex gap-2">
            <button
              className="button button-sm button-danger"
              onClick={handleRejectConfirm}
              disabled={!rejectNotes.trim() || reject.isPending}
              data-testid={`reject-confirm-${rec.id}`}
            >
              {reject.isPending ? 'Rejecting…' : 'Confirm reject'}
            </button>
            <button
              className="button button-sm button-secondary"
              onClick={() => { setShowReject(false); setRejectNotes('') }}
            >
              Cancel
            </button>
          </div>
          {reject.isError && (
            <p className="text-sm mt-1" style={{ color: 'var(--status-error)' }}>
              {(reject.error as Error).message}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function FactorObservationRow({ ob }: { ob: FeaturePerformanceObservation }) {
  const diff = ob.rel_diff_from_baseline
  const diffLabel =
    diff === null
      ? null
      : `${diff >= 0 ? '+' : ''}${(diff * 100).toFixed(0)}% vs. channel average`
  const diffClass = diff === null ? '' : diff >= 0 ? 'factor-observation-diff-up' : 'factor-observation-diff-down'

  return (
    <div className="factor-observation-row">
      <div className="factor-observation-main">
        <span className="factor-observation-feature">{creativeFactorLabel(ob.feature_name)}</span>
        <span className="factor-observation-bucket">{ob.feature_bucket}</span>
      </div>
      <span className="factor-observation-stat">
        {outcomeMetricLabel(ob.metric_name)}: {ob.mean !== null ? formatCompact(ob.mean) : '—'}
      </span>
      {diffLabel && <span className={`factor-observation-stat ${diffClass}`}>{diffLabel}</span>}
      <MaturityBadge maturity={ob.sample_maturity} sampleSize={ob.publication_count} />
    </div>
  )
}

export function Learning() {
  const { workspaceId } = useParams<{ workspaceId: string }>()

  if (!workspaceId) {
    return (
      <div className="page-body">
        <UnavailableState title="No workspace selected" reason="no_data" />
      </div>
    )
  }

  return <LearningContent workspaceId={workspaceId} />
}

function LearningContent({ workspaceId }: { workspaceId: string }) {
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')

  const { channel } = useCurrentChannel(workspaceId)
  const cpChannelId = channel?.id ?? null

  const perf = useChannelPerformance(workspaceId)
  const publicationIds = useMemo(() => perf.items.map(i => i.publication.id), [perf.items])
  const recs = useChannelRecommendations(workspaceId, publicationIds)

  const crossPub = useCrossPublicationLearning(workspaceId, cpChannelId)
  const opportunities = useMarketOpportunities(workspaceId, cpChannelId)
  const experiments = useMarketExperiments(workspaceId, cpChannelId)
  const briefs = useStrategyBriefs(workspaceId, cpChannelId)
  const refreshSchedule = useMarketRefreshSchedule(workspaceId, cpChannelId)

  const filteredRecs = useMemo(
    () =>
      statusFilter === 'all'
        ? recs.recommendations
        : recs.recommendations.filter(r => r.status === statusFilter),
    [recs.recommendations, statusFilter],
  )

  const activeExperimentCount = (experiments.data ?? []).filter(
    e => !['cancelled', 'completed'].includes(e.status),
  ).length

  // ── What's working: per-publication comparison from clean, real analytics ──
  const videosWithData = perf.items
    .filter(i => i.analytics !== null)
    .map(i => ({
      title: i.publication.title,
      avgViewPercentage: i.analytics!.metrics.average_view_percentage ?? null,
      views: i.analytics!.metrics.views ?? null,
      watchTimeSeconds: i.analytics!.metrics.watch_time_seconds ?? null,
    }))
    .filter(v => v.avgViewPercentage !== null)
    .sort((a, b) => (b.avgViewPercentage ?? 0) - (a.avgViewPercentage ?? 0))

  const factorGroups: Record<FeatureGroup, FeaturePerformanceObservation[]> = {
    Script: [], Narration: [], Production: [], Publishing: [],
  }
  for (const ob of crossPub.data?.feature_observations ?? []) {
    const group = CREATIVE_FACTORS.find(f => f.key === ob.feature_name)?.group ?? 'Production'
    factorGroups[group].push(ob)
  }

  const sortedOpportunities = [...(opportunities.data ?? [])].sort(
    (a, b) => (b.composite_score ?? -1) - (a.composite_score ?? -1),
  )

  if (perf.isLoading && perf.items.length === 0) {
    return <LoadingState message="Loading learning data…" />
  }

  return (
    <>
      <PageHeader
        title="Learn"
        subtitle="What the channel is learning, what's working, and what to try next"
      />

      <div className="page-body">
        {/* A. Learning summary */}
        <section className="section">
          <div className="metric-grid">
            <MetricCard
              label="Videos observed"
              value={`${perf.kpis.videosWithDataCount} / ${perf.kpis.videoCount}`}
              sub="have analytics data"
            />
            <MetricCard
              label="Recommendations"
              value={String(recs.recommendations.length)}
              sub={recs.recommendations.length > 0 ? 'from analytics history' : undefined}
            />
            <MetricCard
              label="Market opportunities"
              value={String(opportunities.data?.length ?? 0)}
              sub="tracked from YouTube"
            />
            <MetricCard
              label="Active experiments"
              value={String(activeExperimentCount)}
            />
            <MetricCard
              label="Creative-factor patterns"
              value={String(crossPub.data?.feature_observations.length ?? 0)}
              sub={
                (crossPub.data?.feature_observations.length ?? 0) === 0
                  ? 'cross-publication learning not run yet'
                  : 'observed across publications'
              }
            />
          </div>
        </section>

        {/* B. What's working — channel evidence */}
        <section className="section">
          <SectionHeader
            title="What's working"
            description="How this channel's own videos compare to each other. With only a few videos, treat this as an early signal, not a proven pattern."
          />
          {videosWithData.length >= 2 ? (
            <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
              <div className="table-wrapper">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Video</th>
                      <th>Avg % viewed</th>
                      <th>Views</th>
                      <th>Watch time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {videosWithData.map(v => (
                      <tr key={v.title}>
                        <td>{v.title}</td>
                        <td className="font-mono text-sm">{formatPercent(v.avgViewPercentage)}</td>
                        <td className="font-mono text-sm">{formatCompact(v.views)}</td>
                        <td className="font-mono text-sm">{formatWatchTime(v.watchTimeSeconds)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-secondary" style={{ padding: 'var(--sp-3) var(--sp-4)', margin: 0, borderTop: '1px solid var(--border)' }}>
                Based on {videosWithData.length} video{videosWithData.length === 1 ? '' : 's'} with analytics — too few to call this a reliable pattern yet.
              </p>
            </div>
          ) : (
            <EmptyState
              icon="📊"
              title="Not enough videos yet"
              description="Once at least two videos have analytics data, this section will compare how they're performing against each other."
            />
          )}
        </section>

        {/* C. Recommendations */}
        <section className="section">
          <SectionHeader
            title="Recommendations"
            description="Suggestions generated from this channel's own analytics history. Accepting a recommendation does not auto-apply it."
            actions={
              (recs.recommendations.length > 0 || statusFilter !== 'all') && (
                <div className="flex gap-2">
                  {STATUS_FILTERS.map(f => (
                    <button
                      key={f}
                      className={`button button-sm ${statusFilter === f ? 'button-primary' : 'button-secondary'}`}
                      onClick={() => setStatusFilter(f)}
                      data-testid={`filter-${f}`}
                    >
                      {f}
                    </button>
                  ))}
                </div>
              )
            }
          />
          {recs.isLoading ? (
            <p className="text-sm text-secondary">Loading…</p>
          ) : filteredRecs.length > 0 ? (
            <div className="flex flex-col gap-4">
              {filteredRecs.map(rec => (
                <RecommendationCard key={rec.id} rec={rec} workspaceId={workspaceId} />
              ))}
            </div>
          ) : (
            <UnavailableState
              title="No recommendations yet"
              description="Recommendations are generated from analytics history. They describe associations, not causes."
              reason="no_data"
            />
          )}
        </section>

        {/* D. Creative factors */}
        <section className="section">
          <SectionHeader
            title="Creative factors"
            description="How structural choices — script length, pacing, scene count, publish timing — have been associated with performance across this channel's videos. Always observational, never causal."
          />
          {(crossPub.data?.feature_observations.length ?? 0) > 0 ? (
            <div className="card">
              {FEATURE_GROUPS.map(group =>
                factorGroups[group].length > 0 ? (
                  <div key={group} className="factor-observation-group">
                    <p className="factor-observation-group-title">{group}</p>
                    {factorGroups[group].map(ob => (
                      <FactorObservationRow key={`${ob.feature_name}-${ob.feature_bucket}-${ob.metric_name}`} ob={ob} />
                    ))}
                  </div>
                ) : null,
              )}
            </div>
          ) : (
            <EmptyState
              icon="🧩"
              title="Cross-publication learning hasn't run yet"
              description={
                `This channel tracks ${CREATIVE_FACTORS.length} creative factors across script, narration, ` +
                `production, and publishing — but comparing them needs several videos with both a content ` +
                `feature snapshot and observed analytics. Run 'ace learn cross-pub --channel ` +
                `${cpChannelId ?? '<channel-id>'}' once enough videos have been observed.`
              }
            />
          )}
        </section>

        {/* E. YouTube Trends / Market Intelligence */}
        <section className="section">
          <SectionHeader
            title="YouTube trends"
            description="What currently looks attractive on YouTube, independent of how Orvella itself has performed. A promising trend is not a guarantee — see Recommended experiments for how these connect to a real test."
          />
          <div className="evidence-source-banner evidence-source-external">
            <span className="evidence-source-dot" aria-hidden="true" />
            External market evidence — not Orvella's own performance
          </div>
          {refreshSchedule.data && (
            <p className="text-xs text-secondary" style={{ marginBottom: 'var(--sp-3)' }}>
              {refreshSchedule.data.last_run_at ? (
                <>Last refreshed <LocalTime value={refreshSchedule.data.last_run_at} variant="relative" /></>
              ) : (
                'Not refreshed yet'
              )}
              {refreshSchedule.data.next_run_at && (
                <> · next refresh <LocalTime value={refreshSchedule.data.next_run_at} variant="relative" /></>
              )}
            </p>
          )}
          {sortedOpportunities.length > 0 ? (
            <div className="opportunity-grid">
              {sortedOpportunities.map(o => (
                <OpportunityCard key={o.id} opportunity={o} workspaceId={workspaceId} cpChannelId={cpChannelId!} />
              ))}
            </div>
          ) : (
            <EmptyState
              icon="🌐"
              title="No market opportunities tracked yet"
              description="Opportunities appear here once market intelligence has scanned and scored topics for this channel."
            />
          )}
        </section>

        {/* F. Recommended experiments */}
        <section className="section">
          <SectionHeader
            title="Recommended experiments"
            description="Proposed next tests from the experiment planner, each with its real reasoning — awaiting human review, never auto-scheduled."
          />
          {(briefs.data ?? []).length > 0 ? (
            <div className="flex flex-col gap-4">
              {(briefs.data ?? []).map(b => (
                <StrategyBriefCard key={b.id} brief={b} />
              ))}
            </div>
          ) : (
            <EmptyState
              icon="🧪"
              title="No experiment proposals yet"
              description="Strategy briefs appear here once the experiment planner has run for this channel."
            />
          )}
        </section>

        {/* G. Confidence & evidence model */}
        <section className="section">
          <SectionHeader title="Confidence & evidence model" />
          <div className="card">
            <p className="text-sm text-secondary mb-4">
              Understanding how confidence and evidence are represented across this page:
            </p>
            <div className="flex flex-col gap-3">
              {CONFIDENCE_MODEL.map(item => (
                <div key={item.label} className="diagnostic-finding diagnostic-finding-info">
                  <div>
                    <strong>{item.label}</strong>
                    <p className="mt-1">{item.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>
    </>
  )
}
