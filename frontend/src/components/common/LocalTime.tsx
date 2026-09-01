import { formatDate, formatDateTime, formatFull, formatRelative } from '@/lib/datetime'

type Variant = 'date' | 'datetime' | 'relative'

interface Props {
  /** UTC timestamp from the backend. */
  value: string | null | undefined
  variant?: Variant
  /** Text shown when the value is missing. */
  fallback?: string
  className?: string
}

/** Renders a backend UTC timestamp in the viewer's local timezone.

    Uses a <time> element with a machine-readable dateTime attribute and a
    title carrying the full timestamp, so the precise instant is always
    recoverable on hover without cluttering the layout.
*/
export function LocalTime({
  value,
  variant = 'datetime',
  fallback = '—',
  className,
}: Props) {
  if (!value) return <span className={className}>{fallback}</span>

  const text =
    variant === 'date' ? formatDate(value)
    : variant === 'relative' ? formatRelative(value)
    : formatDateTime(value)

  return (
    <time dateTime={value} title={formatFull(value)} className={className}>
      {text}
    </time>
  )
}
