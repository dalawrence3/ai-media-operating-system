/* Consistent status badge — uses semantic color tokens, never color-only */

interface Props {
  status: string
  label?: string
}

const STATUS_MAP: Record<string, { cls: string; label: string }> = {
  // Workspace/channel/account
  active:       { cls: 'badge-healthy',  label: 'Active' },
  inactive:     { cls: 'badge-neutral',  label: 'Inactive' },
  suspended:    { cls: 'badge-error',    label: 'Suspended' },
  paused:       { cls: 'badge-paused',   label: 'Paused' },
  healthy:      { cls: 'badge-healthy',  label: 'Healthy' },
  degraded:     { cls: 'badge-warn',     label: 'Degraded' },
  unavailable:  { cls: 'badge-error',    label: 'Unavailable' },
  ok:           { cls: 'badge-healthy',  label: 'OK' },

  // Pipeline/operation
  pending:      { cls: 'badge-neutral',  label: 'Pending' },
  running:      { cls: 'badge-info',     label: 'Running' },
  completed:    { cls: 'badge-healthy',  label: 'Completed' },
  failed:       { cls: 'badge-error',    label: 'Failed' },
  cancelled:    { cls: 'badge-neutral',  label: 'Cancelled' },
  blocked:      { cls: 'badge-warn',     label: 'Blocked' },
  waiting_for_review: { cls: 'badge-paused', label: 'Review Required' },
  paused_workspace:   { cls: 'badge-warn',   label: 'Workspace Paused' },

  // Review
  accepted:     { cls: 'badge-healthy',  label: 'Accepted' },
  rejected:     { cls: 'badge-error',    label: 'Rejected' },

  // Budget
  warn:         { cls: 'badge-warn',     label: 'Warn' },
  block:        { cls: 'badge-error',    label: 'Blocked' },

  // Severity
  low:          { cls: 'badge-neutral',  label: 'Low' },
  medium:       { cls: 'badge-warn',     label: 'Medium' },
  high:         { cls: 'badge-error',    label: 'High' },
  critical:     { cls: 'badge-error',    label: 'Critical' },

  // Automation
  manual:       { cls: 'badge-neutral',  label: 'Manual' },
  supervised:   { cls: 'badge-info',     label: 'Supervised' },
  autonomous:   { cls: 'badge-paused',   label: 'Autonomous' },
}

export function StatusBadge({ status, label }: Props) {
  const key = status?.toLowerCase() ?? ''
  const mapped = STATUS_MAP[key]
  const cls   = mapped?.cls ?? 'badge-neutral'
  const text  = label ?? mapped?.label ?? status

  return (
    <span className={`badge ${cls}`} aria-label={`Status: ${text}`}>
      {text}
    </span>
  )
}
