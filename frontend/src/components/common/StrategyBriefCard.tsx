import type { ExperimentStrategyBrief } from '@/api/types'
import { StatusBadge } from '@/components/common/StatusBadge'
import {
  briefStatusLabel,
  confoundingRiskTone,
  experimentStatusLabel,
  outcomeMetricLabel,
  targetDirectionLabel,
} from '@/lib/marketIntelligence'

interface Props {
  brief: ExperimentStrategyBrief
}

const RISK_LABEL: Record<string, string> = {
  low: 'Low confounding risk',
  moderate: 'Moderate confounding risk',
  high: 'High confounding risk',
}

/** One proposed next experiment — a real handoff artifact from the planner,
    not generated copy. Always read-only here: accepting or scheduling a
    brief is a future capability, not part of this page. */
export function StrategyBriefCard({ brief }: Props) {
  const riskTone = confoundingRiskTone(brief.confounding_risk)

  return (
    <div className="card strategy-brief-card">
      <div className="strategy-brief-head">
        <div>
          <p className="strategy-brief-topic">{brief.canonical_topic || brief.market_theme}</p>
          <p className="strategy-brief-hypothesis">{brief.hypothesis}</p>
        </div>
        <span className={`tag strategy-brief-risk-${riskTone}`}>{RISK_LABEL[brief.confounding_risk] ?? brief.confounding_risk}</span>
      </div>

      <div className="strategy-brief-reasons">
        <p>
          <span className="strategy-brief-reason-label">Why now: </span>
          {brief.strategic_reason}
        </p>
        <p>
          <span className="strategy-brief-reason-label">Why it teaches us something: </span>
          {brief.information_gain_reason}
        </p>
      </div>

      <div className="strategy-brief-footer">
        <span className="tag">
          Target: {outcomeMetricLabel(brief.target_metric)} ({targetDirectionLabel(brief.target_direction)})
        </span>
        <span className="tag">{briefStatusLabel(brief.status)}</span>
        {brief.linked_experiment && (
          <StatusBadge
            status={brief.linked_experiment.status}
            label={`Experiment: ${experimentStatusLabel(brief.linked_experiment.status)}`}
          />
        )}
      </div>
    </div>
  )
}
