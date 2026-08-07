/* M14.6 — Learning / Recommendations UI

   CRITICAL SEMANTICS (from Phase 11 contracts):
   - confidence = heuristic signal strength, NOT statistical confidence interval
   - observational recommendation ≠ causal proof
   - Do NOT use causal language: "causes", "increases", "improves", etc.
   - "actionable" strength = confidence ≥ 0.4 AND ≥ 2 unique snapshot IDs
   - Accepting a recommendation does NOT auto-apply it
*/

import { useParams } from 'react-router-dom'
import { UnavailableState } from '@/components/common/UnavailableState'

export function Learning() {
  const { workspaceId } = useParams<{ workspaceId: string }>()

  if (!workspaceId) {
    return (
      <div className="page-body">
        <UnavailableState title="No workspace selected" reason="no_data" />
      </div>
    )
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Learning</h1>
          <p className="page-subtitle">Optimization recommendations from analytics history</p>
        </div>
      </div>

      <div className="page-body">
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

        <div className="section">
          <div className="section-header">
            <h2 className="section-title">Confidence & Evidence Model</h2>
          </div>
          <div className="card">
            <p className="text-sm text-secondary mb-4">
              Understanding recommendation confidence in this system:
            </p>
            <div className="flex flex-col gap-3">
              {[
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
                  desc: 'All Phase 14 recommendations are observational. A recommendation associated with an experiment ID does NOT qualify as controlled-experiment evidence.',
                },
                {
                  label: 'Accept / Reject',
                  desc: 'Human review of recommendations only. Accepting marks the recommendation as accepted — it does not modify any upstream engine, prompt, or configuration.',
                },
              ].map(item => (
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
