/* Number, duration, and metric formatting for user-facing display.

   Product surfaces show human-readable values. Raw floats, second counts, and
   backend identifiers belong behind a technical-details disclosure, not in the
   normal reading path.
*/

/** Locale-aware number, e.g. 10380 → "10,380". */
export function formatNumber(
  value: number | null | undefined,
  maximumFractionDigits = 0,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString(undefined, { maximumFractionDigits })
}

/** Compact number for KPI tiles, e.g. 10380 → "10.4K", 1_250_000 → "1.3M". */
export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  if (Math.abs(value) < 1000) return formatNumber(value)
  return value.toLocaleString(undefined, {
    notation: 'compact',
    maximumFractionDigits: 1,
  })
}

/** Percentage from an already-scaled value (95.57 → "95.6%"). */
export function formatPercent(
  value: number | null | undefined,
  maximumFractionDigits = 1,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value.toLocaleString(undefined, { maximumFractionDigits })}%`
}

/** Duration in seconds → "m:ss" for short clips, "h:mm:ss" beyond an hour. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—'
  const total = Math.max(0, Math.round(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

/** Milliseconds → duration string. Render manifests store durations in ms. */
export function formatDurationMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '—'
  return formatDuration(ms / 1000)
}

/** Watch time in seconds → readable hours/minutes, e.g. 10380 → "2h 53m". */
export function formatWatchTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—'
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return `${total}s`
  const h = Math.floor(total / 3600)
  const m = Math.round((total % 3600) / 60)
  if (h === 0) return `${m}m`
  return m > 0 ? `${h}h ${m}m` : `${h}h`
}

/** Turn a backend snake_case identifier into a readable label.
    "average_view_percentage" → "Average view percentage" */
export function humanizeKey(key: string): string {
  if (!key) return ''
  const spaced = key.replace(/[_-]+/g, ' ').trim()
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

/** Title-case a status/enum token: "credential_invalid" → "Credential invalid". */
export function humanizeStatus(status: string | null | undefined): string {
  if (!status) return 'Unknown'
  return humanizeKey(status)
}
