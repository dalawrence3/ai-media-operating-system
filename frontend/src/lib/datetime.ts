/* Local-timezone date/time formatting.

   The backend stores and returns all timestamps in UTC. Every user-facing
   timestamp must be rendered in the viewer's own timezone using their own
   locale — never a hardcoded 'en-US' and never a raw ISO string.

   These helpers are the single place that conversion happens. Components
   should use <LocalTime> or call these, not construct Dates ad hoc.
*/

/** Parse a backend timestamp into a Date, tolerating missing UTC designators.

    The backend emits both `2026-08-25T19:24:04.670069+00:00` (explicit offset)
    and `2026-08-25T19:24:04` (naive, implicitly UTC). A naive string would be
    interpreted by the JS engine as *local* time, silently shifting it. We append
    'Z' when no timezone designator is present so both forms mean the same instant.
*/
export function parseUtc(value: string | null | undefined): Date | null {
  if (!value) return null
  const trimmed = value.trim()
  if (!trimmed) return null

  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(trimmed)
  const normalized = hasZone ? trimmed : `${trimmed}Z`

  const d = new Date(normalized)
  return Number.isNaN(d.getTime()) ? null : d
}

/** Date only, e.g. "25 Aug 2026" — locale and timezone from the browser. */
export function formatDate(value: string | null | undefined): string {
  const d = parseUtc(value)
  if (!d) return '—'
  return d.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

/** Date and time, e.g. "25 Aug 2026, 19:24". */
export function formatDateTime(value: string | null | undefined): string {
  const d = parseUtc(value)
  if (!d) return '—'
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** Time only, e.g. "19:24". */
export function formatTime(value: string | null | undefined): string {
  const d = parseUtc(value)
  if (!d) return '—'
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

/** Coarse relative time, e.g. "3 hours ago", "in 2 days". */
export function formatRelative(
  value: string | null | undefined,
  now: Date = new Date(),
): string {
  const d = parseUtc(value)
  if (!d) return '—'

  const diffMs = d.getTime() - now.getTime()
  const absMs = Math.abs(diffMs)
  const minute = 60_000
  const hour = 60 * minute
  const day = 24 * hour

  if (absMs < minute) return 'just now'

  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  if (absMs < hour) return rtf.format(Math.round(diffMs / minute), 'minute')
  if (absMs < day) return rtf.format(Math.round(diffMs / hour), 'hour')
  if (absMs < 30 * day) return rtf.format(Math.round(diffMs / day), 'day')
  if (absMs < 365 * day) return rtf.format(Math.round(diffMs / (30 * day)), 'month')
  return rtf.format(Math.round(diffMs / (365 * day)), 'year')
}

/** Full timestamp for tooltips — includes the resolved timezone name. */
export function formatFull(value: string | null | undefined): string {
  const d = parseUtc(value)
  if (!d) return 'Unknown'
  return d.toLocaleString(undefined, {
    dateStyle: 'full',
    timeStyle: 'long',
  })
}
