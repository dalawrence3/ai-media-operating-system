/* Channel-level metric rollups computed from per-publication analytics.
 *
 * There is no channel-level analytics aggregate endpoint. The backend's
 * analytics_aggregates table was found to be reliable for publication 3 but
 * contaminated with leftover seed-provider rows for publication 1 (a
 * 'youtube_dev_seed' row reporting 42,000 views alongside the real 474-view
 * 'youtube' row — see Phase 17B report). To avoid that contamination, these
 * rollups are computed here from each publication's single latest analytics
 * snapshot (GET .../publications/{id}/analytics), which is confirmed clean.
 *
 * This is display-only summation, not analytics business logic: the backend
 * already computed every per-publication number; this file only combines
 * already-correct values for a channel-level view.
 */

export interface PublicationMetrics {
  publicationId: number
  /** Null when the publication has no analytics snapshot yet. */
  metrics: Record<string, number> | null
}

export interface ChannelKpis {
  totalViews: number | null
  totalWatchTimeSeconds: number | null
  totalSubscribersGained: number | null
  totalSubscribersLost: number | null
  totalLikes: number | null
  totalComments: number | null
  totalShares: number | null
  totalEngagedViews: number | null
  /** View-weighted mean of average_view_percentage across videos with data. */
  avgViewPercentage: number | null
  /** Videos included in the publications list, regardless of data availability. */
  videoCount: number
  /** Of those, how many have at least one analytics snapshot. */
  videosWithDataCount: number
}

/** Sum a metric across publications that have data. Missing analytics excludes
    a publication from the sum entirely — it is never treated as zero, mirroring
    the invariant already established in the cross-publication learning engine. */
function sumMetric(items: PublicationMetrics[], metricName: string): number | null {
  const values = items
    .map(i => i.metrics?.[metricName])
    .filter((v): v is number => typeof v === 'number' && !Number.isNaN(v))
  if (values.length === 0) return null
  return values.reduce((a, b) => a + b, 0)
}

/** View-weighted average of average_view_percentage. Videos with zero or
    missing views cannot weight the average and are excluded. */
function weightedAvgViewPercentage(items: PublicationMetrics[]): number | null {
  let weightedSum = 0
  let totalWeight = 0
  for (const item of items) {
    const views = item.metrics?.views
    const avp = item.metrics?.average_view_percentage
    if (typeof views !== 'number' || views <= 0) continue
    if (typeof avp !== 'number' || Number.isNaN(avp)) continue
    weightedSum += views * avp
    totalWeight += views
  }
  return totalWeight > 0 ? weightedSum / totalWeight : null
}

export function computeChannelKpis(items: PublicationMetrics[]): ChannelKpis {
  const withData = items.filter(i => i.metrics !== null)
  return {
    totalViews: sumMetric(items, 'views'),
    totalWatchTimeSeconds: sumMetric(items, 'watch_time_seconds'),
    totalSubscribersGained: sumMetric(items, 'subscribers_gained'),
    totalSubscribersLost: sumMetric(items, 'subscribers_lost'),
    totalLikes: sumMetric(items, 'likes'),
    totalComments: sumMetric(items, 'comments'),
    totalShares: sumMetric(items, 'shares'),
    totalEngagedViews: sumMetric(items, 'engaged_views'),
    avgViewPercentage: weightedAvgViewPercentage(items),
    videoCount: items.length,
    videosWithDataCount: withData.length,
  }
}
