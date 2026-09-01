import { type Maturity, maturityMeta } from '@/lib/maturity'

interface Props {
  maturity: Maturity | null | undefined
  /** Number of observations behind the finding. Shown alongside the badge
      because a maturity label alone hides how thin the evidence is. */
  sampleSize?: number | null
  className?: string
}

/** Evidence-maturity badge.

    The visible label is plain language ("Not enough data", "Directional"), so
    meaning never depends on color alone. The canonical backend token is kept
    in a data attribute and the tooltip, so nothing is lost in translation.
*/
export function MaturityBadge({ maturity, sampleSize, className }: Props) {
  const key = (maturity ?? 'insufficient').toLowerCase()
  const meta = maturityMeta(maturity)

  const sampleText =
    sampleSize === null || sampleSize === undefined
      ? null
      : `${sampleSize} ${sampleSize === 1 ? 'video' : 'videos'}`

  return (
    <span className={`maturity-wrap${className ? ` ${className}` : ''}`}>
      <span
        className={`maturity-badge maturity-${meta.tone}`}
        title={`${meta.label} — ${meta.meaning}`}
        data-maturity={key}
      >
        {meta.label}
      </span>
      {sampleText && <span className="maturity-sample">based on {sampleText}</span>}
    </span>
  )
}
