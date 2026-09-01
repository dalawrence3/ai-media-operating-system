/* Product-facing formatting and labeling for per-video analytics metrics.
   Shared by PublicationDetail and VideoAnalytics so a metric reads the same
   value and label everywhere it appears. */

import { formatCompact, formatDurationMs, formatPercent, formatWatchTime, humanizeKey } from '@/lib/format'

/** A few metric keys read better with a shorter product label than a
    mechanical humanization of their backend name would produce. */
const METRIC_LABEL_OVERRIDES: Record<string, string> = {
  watch_time_seconds: 'Watch time',
  average_view_percentage: 'Avg % viewed',
  average_view_duration: 'Avg view duration',
  engaged_views: 'Engaged views',
  subscribers_gained: 'Subscribers gained',
  subscribers_lost: 'Subscribers lost',
}

export function metricLabel(name: string): string {
  return METRIC_LABEL_OVERRIDES[name] ?? humanizeKey(name)
}

/** Product-facing formatting for a per-video metric value. */
export function formatMetricValue(name: string, value: number): string {
  if (name === 'average_view_percentage') return formatPercent(value)
  if (name === 'average_view_duration') return formatDurationMs(value * 1000)
  if (name === 'watch_time_seconds') return formatWatchTime(value)
  return formatCompact(value)
}

/** Order metrics read best in on a video's performance grid — views and
    watch time first (the numbers people scan for), engagement next, then
    subscriber impact. Unlisted metrics fall back to insertion order. */
const METRIC_DISPLAY_ORDER = [
  'views',
  'watch_time_seconds',
  'average_view_duration',
  'average_view_percentage',
  'engaged_views',
  'likes',
  'comments',
  'shares',
  'subscribers_gained',
  'subscribers_lost',
]

export function sortMetricEntries(metrics: Record<string, number>): [string, number][] {
  const entries = Object.entries(metrics)
  return entries.sort(([a], [b]) => {
    const ia = METRIC_DISPLAY_ORDER.indexOf(a)
    const ib = METRIC_DISPLAY_ORDER.indexOf(b)
    if (ia === -1 && ib === -1) return a.localeCompare(b)
    if (ia === -1) return 1
    if (ib === -1) return -1
    return ia - ib
  })
}
