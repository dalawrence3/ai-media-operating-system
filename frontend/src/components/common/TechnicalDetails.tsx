import type { ReactNode } from 'react'

interface Props {
  /** Disclosure label. Defaults to the standard product wording. */
  summary?: string
  /** Structured data to serialize. Ignored when `children` is provided. */
  data?: unknown
  children?: ReactNode
}

/** Collapsible disclosure for implementation-level detail.

    Normal product pages must not render serialized backend structures in the
    default reading path. Anything that is genuinely useful but technical —
    raw payloads, identifiers, provider responses — goes in here, collapsed.

    Uses a native <details> element so it is keyboard-accessible and
    screen-reader-announced without any custom ARIA wiring.
*/
export function TechnicalDetails({
  summary = 'Technical details',
  data,
  children,
}: Props) {
  const body = children ?? (
    <pre className="technical-details-pre">{safeStringify(data)}</pre>
  )

  return (
    <details className="technical-details">
      <summary className="technical-details-summary">{summary}</summary>
      <div className="technical-details-body">{body}</div>
    </details>
  )
}

function safeStringify(data: unknown): string {
  if (data === undefined || data === null) return 'No data'
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
}
