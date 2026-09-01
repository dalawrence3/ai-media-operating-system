import type { ReactNode } from 'react'

interface Props {
  label: string
  /** Pre-formatted display value. Formatting belongs to the caller via lib/format. */
  value: string
  /** Secondary context line, e.g. "across 2 videos". */
  sub?: string
  /** Short explanation of what the metric means, surfaced as a tooltip. */
  hint?: string
  /** Emphasis for a value that needs attention. Never the only signal — the
      sub line must also carry the meaning, so color is not load-bearing. */
  tone?: 'default' | 'attention'
  icon?: ReactNode
}

/** Primary KPI tile. Larger and more readable than StatTile, which remains
    in use for dense operational counts on Advanced pages. */
export function MetricCard({
  label,
  value,
  sub,
  hint,
  tone = 'default',
  icon,
}: Props) {
  return (
    <div
      className={`metric-card${tone === 'attention' ? ' metric-card-attention' : ''}`}
      title={hint}
    >
      <div className="metric-card-head">
        <span className="metric-card-label">{label}</span>
        {icon && <span className="metric-card-icon" aria-hidden="true">{icon}</span>}
      </div>
      <p className="metric-card-value">{value}</p>
      {sub && <p className="metric-card-sub">{sub}</p>}
    </div>
  )
}
