import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { formatCompact } from '@/lib/format'
import { formatDate } from '@/lib/datetime'

export interface HistoryPoint {
  /** ISO timestamp this observation was ingested. */
  date: string
  value: number
}

interface Props {
  data: HistoryPoint[]
  formatValue?: (value: number) => string
  height?: number
}

const COLOR = 'var(--accent)'

/** A single video's own metric plotted across its real observation history.
 *
 * Unlike VideosBarChart (which compares different videos' latest values),
 * every point here is the same video observed at a different time — a
 * legitimate trend line as long as there are at least two real
 * (observation_state='data') points. The caller is responsible for that
 * threshold check; this component just draws whatever points it is given.
 */
export function VideoHistoryChart({ data, formatValue = formatCompact, height = 220 }: Props) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={formatDate}
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
          cursor={{ stroke: 'var(--border)' }}
          contentStyle={{
            background: 'var(--surface-elevated)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-md)',
            fontSize: 'var(--font-size-sm)',
          }}
          labelFormatter={label => formatDate(String(label))}
          formatter={value => [formatValue(Number(value)), '']}
        />
        <Line
          type="monotone"
          dataKey="value"
          stroke={COLOR}
          strokeWidth={2}
          dot={{ r: 4, fill: COLOR, strokeWidth: 0 }}
          activeDot={{ r: 6 }}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
