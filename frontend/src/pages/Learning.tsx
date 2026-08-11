/* M14.6 — Learning / Recommendations UI

   CRITICAL SEMANTICS (from Phase 11 contracts):
   - confidence = heuristic signal strength, NOT statistical confidence interval
   - observational recommendation ≠ causal proof
   - Do NOT use causal language: "causes", "increases", "improves", etc.
   - "actionable" strength = confidence ≥ 0.4 AND ≥ 2 unique snapshot IDs
   - Accepting a recommendation does NOT auto-apply it
*/

import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { UnavailableState } from '@/components/common/UnavailableState'
import { useRecommendations, useAcceptRecommendation, useRejectRecommendation } from '@/hooks/useLearning'
import type { OptimizationRecommendation } from '@/api/types'

const STATUS_FILTERS = ['all', 'pending', 'accepted', 'rejected'] as const
type StatusFilter = (typeof STATUS_FILTERS)[number]

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
    desc: 'All Phase 14 recommendations are observational. A recommendation associated with an experiment ID does NOT qualify as controlled-experiment evidence. They describe associations, not causes.',
  },
  {
    label: 'Accept / Reject',
    desc: 'Human review of recommendations only. Accepting marks the recommendation as accepted — it does not modify any upstream engine, prompt, or configuration.',
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
  const { data: recs, isLoading } = useRecommendations(
    workspaceId,
    statusFilter === 'all' ? undefined : statusFilter,
  )

  const hasData = !isLoading && recs && recs.length > 0
  // Keep filter buttons visible when a non-"all" filter is active so users can
  // un-filter back to all even when the filtered result set is empty.
  const showFilters = hasData || statusFilter !== 'all'

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Learning</h1>
          <p className="page-subtitle">Optimization recommendations from analytics history</p>
        </div>
        {showFilters && (
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
        )}
      </div>

      <div className="page-body">
        {isLoading ? (
          <div className="section">
            <div className="card">
              <p className="text-sm text-secondary">Loading recommendations…</p>
            </div>
          </div>
        ) : hasData ? (
          <div className="section">
            <div className="section-header">
              <h2 className="section-title">Recommendations</h2>
              <span className="text-sm text-secondary">{recs.length} result{recs.length !== 1 ? 's' : ''}</span>
            </div>
            <div className="flex flex-col gap-4">
              {recs.map(rec => (
                <RecommendationCard key={rec.id} rec={rec} workspaceId={workspaceId} />
              ))}
            </div>
          </div>
        ) : (
          <div className="section">
            <UnavailableState
              title="Learning center — No analytics data yet"
              description={
                `Optimization recommendations are generated from your analytics history. ` +
                `Run 'ace learn analyze <publication_id>' after collecting analytics data. ` +
                `Recommendations are observational — they describe associations, not causes.`
              }
              reason="no_data"
            />
          </div>
        )}

        <div className="section">
          <div className="section-header">
            <h2 className="section-title">Confidence & Evidence Model</h2>
          </div>
          <div className="card">
            <p className="text-sm text-secondary mb-4">
              Understanding recommendation confidence in this system:
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
        </div>
      </div>
    </>
  )
}
