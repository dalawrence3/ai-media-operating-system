import { useState } from 'react'
import type { MarketOpportunity } from '@/api/types'
import {
  OPPORTUNITY_FACTORS,
  competitionLabel,
  lifecycleLabel,
  scoreLabel,
  strategicRoleLabel,
} from '@/lib/marketIntelligence'
import { formatPercent } from '@/lib/format'
import { LocalTime } from '@/components/common/LocalTime'
import { useOpportunityEvidence } from '@/hooks/useLearning'

interface Props {
  opportunity: MarketOpportunity
  workspaceId: string
  cpChannelId: string
}

/** External YouTube market signal for a topic — distinct from Orvella's own
    channel-performance evidence, which lives in its own section entirely.
    A topic appearing here means the market looks attractive; it says
    nothing about whether Orvella would succeed with it. */
export function OpportunityCard({ opportunity: o, workspaceId, cpChannelId }: Props) {
  const [showEvidence, setShowEvidence] = useState(false)
  const label = o.canonical_label || o.title || o.normalized_topic

  return (
    <div className="card opportunity-card">
      <div className="opportunity-card-head">
        <div>
          <p className="opportunity-card-title">{label}</p>
          <div className="opportunity-card-badges">
            <span className="tag">{strategicRoleLabel(o.strategic_role)}</span>
            <span className="tag">{lifecycleLabel(o.current_lifecycle_state)}</span>
          </div>
        </div>
        {o.composite_score !== null && (
          <div className="opportunity-card-score" title="Composite attractiveness score">
            <span className="opportunity-card-score-value">{formatPercent(o.composite_score * 100, 0)}</span>
            <span className="opportunity-card-score-label">attractiveness</span>
          </div>
        )}
      </div>

      <div className="opportunity-card-factors">
        {OPPORTUNITY_FACTORS.map(f => {
          const value = o[f.key]
          const status = o[f.statusKey]
          const display = f.key === 'score_competition' ? competitionLabel(value, status) : scoreLabel(value, status)
          const isAbsent = value === null || value === undefined
          return (
            <div key={f.key} className="opportunity-factor" title={f.hint}>
              <span className="opportunity-factor-label">{f.label}</span>
              <span className={`opportunity-factor-value${isAbsent ? ' opportunity-factor-value-absent' : ''}`}>
                {display}
              </span>
            </div>
          )
        })}
      </div>

      <div className="opportunity-card-footer">
        {o.confidence !== null && <span>Confidence {formatPercent(o.confidence * 100, 0)}</span>}
        <button
          type="button"
          className="opportunity-evidence-toggle"
          onClick={() => setShowEvidence(v => !v)}
          aria-expanded={showEvidence}
        >
          {o.evidence_count} evidence signal{o.evidence_count === 1 ? '' : 's'} {showEvidence ? '▾' : '▸'}
        </button>
      </div>

      {showEvidence && (
        <EvidencePanel workspaceId={workspaceId} opportunityId={o.id} cpChannelId={cpChannelId} />
      )}
    </div>
  )
}

function EvidencePanel({
  workspaceId,
  opportunityId,
  cpChannelId,
}: {
  workspaceId: string
  opportunityId: number
  cpChannelId: string
}) {
  const evidence = useOpportunityEvidence(workspaceId, opportunityId, cpChannelId)

  return (
    <div className="opportunity-evidence-panel">
      <div className="evidence-source-banner evidence-source-external" style={{ marginBottom: 'var(--sp-2)' }}>
        <span className="evidence-source-dot" aria-hidden="true" />
        External market evidence — why the system considers this opportunity interesting
      </div>
      {evidence.isLoading && <p className="text-sm text-secondary">Loading…</p>}
      {evidence.data && evidence.data.snapshots.length === 0 && (
        <p className="text-sm text-secondary">No evidence recorded yet.</p>
      )}
      {evidence.data?.snapshots.map(snap => (
        <div key={snap.source_label} className="evidence-snapshot">
          <p className="evidence-snapshot-time">
            Observed <LocalTime value={snap.collected_at} variant="relative" />
          </p>
          <div className="evidence-snapshot-items">
            {snap.items.map(item => (
              <div key={item.evidence_type} className="evidence-item">
                <span className="evidence-item-label">{item.label}</span>
                <span className="evidence-item-value">
                  {item.text ?? (item.value !== null ? item.value.toFixed(item.unit === 'id' ? 0 : 3) : '—')}
                </span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
