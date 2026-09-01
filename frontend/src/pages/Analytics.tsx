/* Phase 17C — Analytics: channel performance and video-by-video breakdown.

   Answers "how is this channel performing, and how is each video doing?"
   Market intelligence / trend research belongs to Learn (Phase 17D), not here.

   Data source: per-publication analytics, fanned out via useChannelPerformance
   (see hooks/useChannelPerformance.ts) — the same clean, publication-scoped
   path the Dashboard and PublicationDetail already use. This deliberately
   avoids two known-bad backend paths (see Phase 17B/17C reports):
     - analytics_aggregates: carries a contaminated 'youtube_dev_seed' row
       for publication 1 alongside the real 'youtube' row.
     - the workspace-wide /analytics/aggregates and /analytics/snapshots
       endpoints: resolve scope via workspace_topic_ids, which returns []
       for any topic with a NULL workspace_id (e.g. topic_id=4 — publication
       3's topic), silently dropping that publication's data.
*/

import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { PageHeader } from '@/components/common/PageHeader'
import { SectionHeader } from '@/components/common/SectionHeader'
import { MetricCard } from '@/components/common/MetricCard'
import { VideoCard, type VideoCardData } from '@/components/common/VideoCard'
import { VideosBarChart, type VideoBarDatum } from '@/components/common/VideosBarChart'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { useCurrentChannel } from '@/hooks/useCurrentChannel'
import { useChannelPerformance, type PublicationWithAnalytics } from '@/hooks/useChannelPerformance'
import { formatCompact, formatPercent, formatWatchTime } from '@/lib/format'

type ChartMetric = 'views' | 'watch_time_seconds' | 'average_view_percentage' | 'likes' | 'engaged_views' | 'subscribers_gained'

const CHART_METRICS: { key: ChartMetric; label: string; format: (v: number) => string }[] = [
  { key: 'views', label: 'Views', format: formatCompact },
  { key: 'watch_time_seconds', label: 'Watch time', format: formatWatchTime },
  { key: 'average_view_percentage', label: 'Avg % viewed', format: v => formatPercent(v) },
  { key: 'likes', label: 'Likes', format: formatCompact },
  { key: 'engaged_views', label: 'Engaged views', format: formatCompact },
  { key: 'subscribers_gained', label: 'Subscribers gained', format: formatCompact },
]

type SortKey = 'newest' | 'views' | 'avgViewPercentage' | 'watchTime'

const SORTS: { key: SortKey; label: string }[] = [
  { key: 'newest', label: 'Newest' },
  { key: 'views', label: 'Most viewed' },
  { key: 'avgViewPercentage', label: 'Highest % viewed' },
  { key: 'watchTime', label: 'Most watch time' },
]

function metricOf(item: PublicationWithAnalytics, name: string): number | null {
  const v = item.analytics?.metrics[name]
  return typeof v === 'number' && !Number.isNaN(v) ? v : null
}

function sortItems(items: PublicationWithAnalytics[], sort: SortKey): PublicationWithAnalytics[] {
  const arr = [...items]
  switch (sort) {
    case 'newest':
      return arr.sort((a, b) => (b.publication.published_at ?? '').localeCompare(a.publication.published_at ?? ''))
    case 'views':
      return arr.sort((a, b) => (metricOf(b, 'views') ?? -1) - (metricOf(a, 'views') ?? -1))
    case 'avgViewPercentage':
      return arr.sort(
        (a, b) => (metricOf(b, 'average_view_percentage') ?? -1) - (metricOf(a, 'average_view_percentage') ?? -1),
      )
    case 'watchTime':
      return arr.sort((a, b) => (metricOf(b, 'watch_time_seconds') ?? -1) - (metricOf(a, 'watch_time_seconds') ?? -1))
  }
}

function toCardData(item: PublicationWithAnalytics): VideoCardData {
  const pub = item.publication
  return {
    id: pub.id,
    title: pub.title,
    providerVideoId: pub.provider_video_id,
    visibility: pub.visibility,
    status: pub.status,
    publishedAt: pub.published_at,
    durationMs: pub.render_duration_ms,
    views: metricOf(item, 'views'),
    avgViewPercentage: metricOf(item, 'average_view_percentage'),
    watchTimeSeconds: metricOf(item, 'watch_time_seconds'),
    topicTitle: pub.topic_title,
  }
}

export function Analytics() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''
  const navigate = useNavigate()
  const [chartMetric, setChartMetric] = useState<ChartMetric>('views')
  const [sort, setSort] = useState<SortKey>('newest')

  const { primaryAccount } = useCurrentChannel(wid)
  const perf = useChannelPerformance(wid)

  const chartData: VideoBarDatum[] = useMemo(
    () =>
      perf.items
        .filter(i => metricOf(i, chartMetric) !== null)
        .map(i => ({
          label: i.publication.title.length > 18 ? `${i.publication.title.slice(0, 16)}…` : i.publication.title,
          title: i.publication.title,
          value: metricOf(i, chartMetric)!,
        })),
    [perf.items, chartMetric],
  )
  const activeMetric = CHART_METRICS.find(m => m.key === chartMetric)!

  const sortedItems = useMemo(() => sortItems(perf.items, sort), [perf.items, sort])

  if (!wid) {
    return (
      <div className="page-body">
        <EmptyState icon="🏢" title="No workspace selected" />
      </div>
    )
  }

  if (perf.isLoading && perf.items.length === 0) {
    return <LoadingState message="Loading analytics…" />
  }
  if (perf.error) {
    return <ErrorState error={perf.error} />
  }

  const { kpis } = perf

  return (
    <>
      <PageHeader
        title="Analytics"
        subtitle={
          primaryAccount
            ? `${primaryAccount.display_name} · Channel performance and video-by-video breakdown`
            : 'Channel performance and video-by-video breakdown'
        }
      />

      <div className="page-body">
        {perf.items.length === 0 ? (
          <EmptyState
            icon="📊"
            title="No videos yet"
            description="Analytics will appear here once the channel has published videos."
          />
        ) : (
          <>
            {/* Channel KPIs */}
            <section className="section">
              <div className="metric-grid">
                <MetricCard
                  label="Views"
                  value={formatCompact(kpis.totalViews)}
                  sub={
                    kpis.videosWithDataCount > 0
                      ? `across ${kpis.videosWithDataCount} of ${kpis.videoCount} videos`
                      : 'No analytics data yet'
                  }
                />
                <MetricCard
                  label="Watch time"
                  value={formatWatchTime(kpis.totalWatchTimeSeconds)}
                  sub={kpis.videosWithDataCount > 0 ? 'total across observed videos' : undefined}
                />
                <MetricCard
                  label="Avg % viewed"
                  value={formatPercent(kpis.avgViewPercentage)}
                  sub={kpis.avgViewPercentage !== null ? 'view-weighted across videos' : undefined}
                />
                <MetricCard
                  label="Engaged views"
                  value={formatCompact(kpis.totalEngagedViews)}
                  hint="Views the provider counted as meaningfully engaged, not just started."
                />
                <MetricCard label="Likes" value={formatCompact(kpis.totalLikes)} />
                <MetricCard
                  label="Subscribers gained"
                  value={formatCompact(kpis.totalSubscribersGained)}
                  sub={kpis.videosWithDataCount > 0 ? 'attributed to these videos' : undefined}
                />
              </div>
            </section>

            {/* Performance over time */}
            <section className="section">
              <SectionHeader
                title="Performance over time"
                description="Latest observed value per video. Individual videos currently have only a handful of analytics observations spanning a few days, so a single combined channel trend line would be misleading — this compares videos side by side instead. Open a video below for its own real observation history."
                actions={
                  <div className="segmented" role="group" aria-label="Metric">
                    {CHART_METRICS.map(m => (
                      <button
                        key={m.key}
                        className={`btn btn-sm ${chartMetric === m.key ? 'btn-primary' : 'btn-ghost'}`}
                        onClick={() => setChartMetric(m.key)}
                        aria-pressed={chartMetric === m.key}
                      >
                        {m.label}
                      </button>
                    ))}
                  </div>
                }
              />
              <div className="card">
                {chartData.length > 0 ? (
                  <VideosBarChart data={chartData} formatValue={activeMetric.format} />
                ) : (
                  <EmptyState
                    icon="📊"
                    title="No analytics data yet"
                    description="Performance data will appear here once this metric has been observed for at least one video."
                  />
                )}
              </div>
            </section>

            {/* Video performance */}
            <section className="section">
              <SectionHeader
                title="Video performance"
                description="Every video this channel has published, with whatever analytics have been observed so far."
                actions={
                  <div className="segmented" role="group" aria-label="Sort videos">
                    {SORTS.map(s => (
                      <button
                        key={s.key}
                        className={`btn btn-sm ${sort === s.key ? 'btn-primary' : 'btn-ghost'}`}
                        onClick={() => setSort(s.key)}
                        aria-pressed={sort === s.key}
                      >
                        {s.label}
                      </button>
                    ))}
                  </div>
                }
              />
              <div className="video-grid">
                {sortedItems.map(item => (
                  <VideoCard
                    key={item.publication.id}
                    video={toCardData(item)}
                    onClick={() => navigate(`/workspaces/${wid}/analytics/${item.publication.id}`)}
                  />
                ))}
              </div>
            </section>
          </>
        )}
      </div>
    </>
  )
}
