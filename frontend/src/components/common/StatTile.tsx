interface Props {
  label: string
  value: string | number
  sub?: string
  accent?: boolean
}

export function StatTile({ label, value, sub, accent }: Props) {
  return (
    <div className="stat-tile">
      <p className="stat-tile-label">{label}</p>
      <p
        className="stat-tile-value"
        style={accent ? { color: 'var(--accent)' } : undefined}
      >
        {value}
      </p>
      {sub && <p className="stat-tile-sub">{sub}</p>}
    </div>
  )
}
