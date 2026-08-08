/* Explicit unavailable/setup-required state — not "Unknown error".
   Use when a backend integration is intentionally gated or not yet set up. */

interface Props {
  title: string
  description?: string
  reason?: 'provider_setup_required' | 'live_disabled' | 'no_data' | 'coming_soon' | string
}

const REASON_ICONS: Record<string, string> = {
  provider_setup_required: '🔌',
  live_disabled: '🔒',
  no_data: '📊',
  coming_soon: '🚧',
}

export function UnavailableState({ title, description, reason }: Props) {
  const icon = reason ? (REASON_ICONS[reason] ?? '⚠️') : '⚠️'
  return (
    <div className="unavailable-state">
      <span style={{ fontSize: 32 }} aria-hidden="true">{icon}</span>
      <p className="unavailable-state-title">{title}</p>
      {description && <p className="unavailable-state-desc">{description}</p>}
    </div>
  )
}
