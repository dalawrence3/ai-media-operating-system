import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { formatCompact } from '@/lib/format'

export interface VideoBarDatum {
  /** Short label for the x-axis, e.g. a truncated title. */
  label: string
  value: number
  /** Full title, shown in the tooltip. */
  title: string
}

interface Props {
  data: VideoBarDatum[]
  /** Axis/tooltip value formatter, e.g. views vs. a percentage vs. a duration. */
  formatValue?: (value: number) => string
  height?: number
}

const COLOR = 'var(--accent)'

/** Per-video bar comparison.
 *
 * A time-series line chart was deliberately not used here: the channel
 * currently has only 1–2 analytics observations per video, spanning about a
 * week. A line through two points would visually imply a trend that isn't
 * statistically established. A per-video bar comparison uses the same data
 * honestly — it needs only one current value per video — and becomes a
 * candidate to graduate into a real time series once enough daily
 * observations accumulate.
 */
export function VideosBarChart({ data, formatValue = formatCompact, height = 260 }: Props) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
          tickLine={false}
          axisLine={{ stroke: 'var(--border)' }}
        />
        <YAxis
          tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
          tickLine={false}
          axisLine={false}
          tickFormatter={formatValue}
          width={48}
        />
        <Tooltip
          cursor={{ fill: 'var(--surface-bg)' }}
          contentStyle={{
            background: 'var(--surface-elevated)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-md)',
            fontSize: 'var(--font-size-sm)',
          }}
          labelStyle={{ color: 'var(--text-primary)', fontWeight: 600 }}
          formatter={(value) => [formatValue(Number(value)), '']}
          labelFormatter={(_label, payload) =>
            (payload?.[0]?.payload as VideoBarDatum | undefined)?.title ?? ''
          }
        />
        <Bar dataKey="value" fill={COLOR} radius={[4, 4, 0, 0]} maxBarSize={64} />
      </BarChart>
    </ResponsiveContainer>
  )
}
