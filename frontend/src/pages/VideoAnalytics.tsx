/* Phase 17C — individual video analytics.

   Analyzing one video: identity, current performance, real observation
   history (when there's enough of it to be honest), retention (only if the
   provider has actually returned retention points — none has yet, for any
   video in this workspace), and context (experiment lineage, a teaser link
   into Learn). This does not duplicate Learn's job — it reports performance,
   it does not recommend anything.
*/

import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { PageHeader } from '@/components/common/PageHeader'
import { SectionHeader } from '@/components/common/SectionHeader'
import { StatusBadge } from '@/components/common/StatusBadge'
import { MaturityBadge } from '@/components/common/MaturityBadge'
import { MetricCard } from '@/components/common/MetricCard'
import { VideoThumbnail } from '@/components/common/VideoThumbnail'
import { VideoHistoryChart, type HistoryPoint } from '@/components/common/VideoHistoryChart'
import { LocalTime } from '@/components/common/LocalTime'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { TechnicalDetails } from '@/components/common/TechnicalDetails'
import {
  usePublication,
  usePublicationAnalytics,
  usePublicationAnalyticsHistory,
} from '@/hooks/usePublications'
import { useRecommendations } from '@/hooks/useLearning'
import { formatCompact, formatDurationMs, formatPercent, formatWatchTime } from '@/lib/format'
import { formatMetricValue, metricLabel, sortMetricEntries } from '@/lib/analyticsMetrics'
import type { PublicationAnalyticsHistoryEntry } from '@/api/types'

function MetaRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="detail-meta-row">
      <span className="detail-meta-label">{label}</span>
      <span className="detail-meta-value">{value}</span>
    </div>
  )
}

const HISTORY_METRICS: { key: string; label: string; format: (v: number) => string }[] = [
  { key: 'views', label: 'Views', format: formatCompact },
  { key: 'watch_time_seconds', label: 'Watch time', format: formatWatchTime },
  { key: 'average_view_percentage', label: 'Avg % viewed', format: v => formatPercent(v) },
]

/** A history entry's `metrics` is `{}` when the provider had nothing to
    report yet — that's a real absence, not a zero, so it is excluded here
    rather than plotted as 0. */
function pointsForMetric(history: PublicationAnalyticsHistoryEntry[], metric: string): HistoryPoint[] {
  return history
    .filter(h => typeof h.metrics[metric] === 'number')
    .map(h => ({ date: h.ingested_at, value: h.metrics[metric] }))
}

export function VideoAnalytics() {
  const { workspaceId, publicationId } = useParams<{ workspaceId: string; publicationId: string }>()
  const navigate = useNavigate()
  const wid = workspaceId ?? ''
  const pubId = publicationId ? parseInt(publicationId, 10) : null

  const { data: pub, isLoading: pubLoading, error: pubError } = usePublication(wid, pubId)
  const { data: analytics, isLoading: analyticsLoading } = usePublicationAnalytics(wid, pubId)
  const { data: history, isLoading: historyLoading } = usePublicationAnalyticsHistory(wid, pubId)
  const { recommendations } = useRecommendationsForVideo(wid, pubId)

  const chartableMetrics = useMemo(
    () => HISTORY_METRICS.filter(m => pointsForMetric(history ?? [], m.key).length >= 2),
    [history],
  )
  const [historyMetric, setHistoryMetric] = useState<string | null>(null)
  const activeHistoryMetric = historyMetric && chartableMetrics.some(m => m.key === historyMetric)
    ? historyMetric
    : chartableMetrics[0]?.key ?? null
  const activeMetricDef = HISTORY_METRICS.find(m => m.key === activeHistoryMetric)
  const historyPoints = activeHistoryMetric ? pointsForMetric(history ?? [], activeHistoryMetric) : []
  const dataObservationCount = (history ?? []).filter(h => h.observation_state === 'data').length

  if (pubLoading) {
    return <LoadingState message="Loading video analytics…" />
  }
  if (pubError || !pub) {
    return (
      <div className="page-body">
        <button className="btn btn-ghost btn-sm" onClick={() => navigate(`/workspaces/${wid}/analytics`)}>
          ← Analytics
        </button>
        <ErrorState error={pubError ?? new Error('Video not found.')} />
      </div>
    )
  }

  return (
    <>
      <div style={{ padding: 'var(--sp-4) var(--sp-8) 0' }}>
        <button className="btn btn-ghost btn-sm" onClick={() => navigate(`/workspaces/${wid}/analytics`)}>
          ← Analytics
        </button>
      </div>

      <PageHeader
        title={pub.title}
        subtitle={pub.topic_title ?? undefined}
        actions={
          <>
            <StatusBadge status={pub.visibility} />
            <StatusBadge status={pub.status} />
          </>
        }
      />

      <div
        className="page-body"
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 320px',
          gap: 'var(--sp-6)',
          alignItems: 'start',
        }}
      >
        {/* Main column */}
        <div>
          {/* Performance */}
          <section className="section">
            <SectionHeader title="Performance" description="Latest observed values for this video." />
            {analyticsLoading && <p className="text-sm text-secondary">Loading…</p>}
            {!analyticsLoading && analytics && (
              analytics.snapshot_id === null ? (
                <EmptyState
                  icon="📊"
                  title="No analytics data yet"
                  description="This video will be observed automatically once analytics collection runs."
                />
              ) : (
                <>
                  <div className="metric-grid">
                    {sortMetricEntries(analytics.metrics).map(([name, value]) => (
                      <MetricCard key={name} label={metricLabel(name)} value={formatMetricValue(name, value)} />
                    ))}
                  </div>
                  <p className="text-sm text-secondary" style={{ marginTop: 'var(--sp-3)' }}>
                    Observed <LocalTime value={analytics.snapshot_ingested_at} variant="relative" />
                  </p>
                </>
              )
            )}
          </section>

          {/* History */}
          <section className="section">
            <SectionHeader
              title="Performance over time"
              description="This video's own real observation history — every point is the same video measured again later."
              actions={
                chartableMetrics.length > 1 && (
                  <div className="segmented" role="group" aria-label="History metric">
                    {chartableMetrics.map(m => (
                      <button
                        key={m.key}
                        className={`btn btn-sm ${activeHistoryMetric === m.key ? 'btn-primary' : 'btn-ghost'}`}
                        onClick={() => setHistoryMetric(m.key)}
                        aria-pressed={activeHistoryMetric === m.key}
                      >
                        {m.label}
                      </button>
                    ))}
                  </div>
                )
              }
            />
            <div className="card">
              {historyLoading ? (
                <p className="text-sm text-secondary">Loading…</p>
              ) : historyPoints.length >= 2 && activeMetricDef ? (
                <VideoHistoryChart data={historyPoints} formatValue={activeMetricDef.format} />
              ) : (
                <EmptyState
                  icon="📈"
                  title="Not enough observation history yet"
                  description={
                    dataObservationCount === 0
                      ? 'No analytics observation has reported data for this video yet.'
                      : dataObservationCount === 1
                      ? 'Only one observation has reported data so far — a trend needs at least two.'
                      : 'Observations so far don’t share a common reported metric yet — check back after the next analytics run.'
                  }
                />
              )}
            </div>
          </section>

          {/* Retention */}
          <section className="section">
            <SectionHeader title="Retention" description="How far into the video viewers typically watch." />
            <div className="card">
              {analytics && analytics.retention_point_count > 0 ? (
                <p className="text-sm" style={{ margin: 0 }}>
                  {analytics.retention_point_count} retention points recorded for this video.
                </p>
              ) : (
                <EmptyState
                  icon="📉"
                  title="Retention data not available"
                  description="No retention curve has been reported for this video yet."
                />
              )}
            </div>
          </section>
        </div>

        {/* Sidebar */}
        <div>
          <div className="card" style={{ marginBottom: 'var(--sp-3)', padding: 0, overflow: 'hidden' }}>
            <VideoThumbnail
              videoId={pub.provider_video_id}
              title={pub.title}
              isPrivate={pub.visibility === 'private'}
              size="md"
            />
          </div>

          <div className="card" style={{ marginBottom: 'var(--sp-3)' }}>
            <MetaRow label="Publish date" value={<LocalTime value={pub.published_at} />} />
            <MetaRow label="Duration" value={formatDurationMs(pub.render_duration_ms)} />
            {pub.provider_url && (
              <MetaRow
                label="YouTube"
                value={
                  <a href={pub.provider_url} target="_blank" rel="noopener noreferrer" className="text-sm">
                    View on YouTube ↗
                  </a>
                }
              />
            )}
            <MetaRow
              label="Manage"
              value={
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => navigate(`/workspaces/${wid}/content/${pubId}`)}
                >
                  Watch & manage →
                </button>
              }
            />
          </div>

          {(analytics?.experiment_id || recommendations.length > 0) && (
            <div className="card" style={{ marginBottom: 'var(--sp-3)' }}>
              <h3 className="detail-meta-label" style={{ marginBottom: 'var(--sp-2)' }}>Context</h3>
              {analytics?.experiment_id && (
                <p className="text-sm text-secondary" style={{ margin: '0 0 var(--sp-2)' }}>
                  Associated with experiment <code>{analytics.experiment_id}</code>.
                </p>
              )}
              {recommendations.length > 0 && (
                <>
                  <p className="text-sm text-secondary" style={{ margin: '0 0 var(--sp-2)' }}>
                    {recommendations.length} learning {recommendations.length === 1 ? 'signal' : 'signals'} reference
                    this video.
                  </p>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)' }}>
                    {recommendations.slice(0, 2).map(r => (
                      <div key={r.id} style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)' }}>
                        <MaturityBadge maturity={r.recommendation_strength} />
                        <span className="text-sm" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {r.title}
                        </span>
                      </div>
                    ))}
                  </div>
                  <button className="btn btn-ghost btn-sm" style={{ marginTop: 'var(--sp-2)' }} onClick={() => navigate(`/workspaces/${wid}/learn`)}>
                    View in Learn →
                  </button>
                </>
              )}
            </div>
          )}

          <TechnicalDetails summary="Render & lineage details">
            <div className="detail-meta-list">
              <MetaRow label="Resolution" value={pub.render_width && pub.render_height ? `${pub.render_width}×${pub.render_height}` : '—'} />
              <MetaRow label="FPS" value={pub.render_fps ?? '—'} />
              <MetaRow label="Render status" value={pub.render_status ?? '—'} />
              <MetaRow label="Provider" value={pub.provider} />
              <MetaRow label="Created" value={<LocalTime value={pub.created_at} />} />
            </div>
          </TechnicalDetails>
        </div>
      </div>
    </>
  )
}

/** Recommendations that reference this specific publication — excludes dev
    fixture recommendations the same way useChannelRecommendations does. */
function useRecommendationsForVideo(workspaceId: string, publicationId: number | null) {
  const { data } = useRecommendations(workspaceId, undefined, undefined, publicationId ?? undefined)
  const recommendations = (data ?? []).filter(r => !r.title.startsWith('[DEV]'))
  return { recommendations }
}
