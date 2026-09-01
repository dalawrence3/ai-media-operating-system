/* Phase 17B — Dashboard: channel command center.

   Answers, within a glance: how is the channel doing, what happened
   recently, what's in progress, what is the system learning, and does
   anything need attention.

   Data sources are deliberately narrow — see hooks/useChannelPerformance.ts
   and hooks/useChannelRecommendations for why per-publication fan-out is used
   instead of the workspace-wide analytics/recommendations/aggregates
   endpoints (both have confirmed gaps for this workspace; see the Phase 17B
   report).
*/

import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { PageHeader } from '@/components/common/PageHeader'
import { SectionHeader } from '@/components/common/SectionHeader'
import { MetricCard } from '@/components/common/MetricCard'
import { MaturityBadge } from '@/components/common/MaturityBadge'
import { StatusBadge } from '@/components/common/StatusBadge'
import { VideoCard, type VideoCardData } from '@/components/common/VideoCard'
import { VideosBarChart, type VideoBarDatum } from '@/components/common/VideosBarChart'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { LocalTime } from '@/components/common/LocalTime'
import { useCurrentChannel } from '@/hooks/useCurrentChannel'
import {
  useChannelAutomationPolicy,
  useChannelPublishingAuthorization,
} from '@/hooks/useChannel'
import { useChannelPerformance } from '@/hooks/useChannelPerformance'
import { useChannelRecommendations } from '@/hooks/useLearning'
import { useHealth, useReviewQueue, useExceptionQueue } from '@/hooks/useWorkspace'
import { formatCompact, formatPercent, formatWatchTime, humanizeStatus } from '@/lib/format'

type ChartMetric = 'views' | 'watch_time_seconds' | 'average_view_percentage'

const CHART_METRICS: { key: ChartMetric; label: string; format: (v: number) => string }[] = [
  { key: 'views', label: 'Views', format: formatCompact },
  { key: 'watch_time_seconds', label: 'Watch time', format: formatWatchTime },
  { key: 'average_view_percentage', label: 'Avg % viewed', format: v => formatPercent(v) },
]

const RECOVERABLE_ACCOUNT_STATUSES = new Set([
  'credential_invalid',
  'credential_expiring',
  'quota_limited',
  'disconnected',
])

/** Compact publishing status — a glance, not a control panel.

    Deliberately read-only and deliberately small: everything actionable lives
    on the Channel page, and duplicating those controls here would give the
    operator two places to change the same safety-critical setting. Renders
    nothing at all until automation is actually configured, so a workspace not
    using autonomy never sees an empty widget. */
function PublishingStatusStrip({ workspaceId, channelId }: { workspaceId: string; channelId: string }) {
  const automation = useChannelAutomationPolicy(workspaceId, channelId)
  const auth = useChannelPublishingAuthorization(workspaceId, channelId)

  const policy = automation.data?.policy ?? null
  const decision = auth.data?.decision
  if (!policy || !decision) return null

  const slots = automation.data?.active_slots ?? []
  const nextSlot = slots.find(s => s.state === 'filled' || s.state === 'reserved')
  const readySlot = slots.find(s => s.production_status === 'ready')

  // A channel that is authorized and blocked for no reason other than the
  // rolling 24h rate ceiling is healthy, not faulting — it will resume on
  // its own once the window clears. Collapsing that into the same "Blocked"
  // label as a real fault (bad credential, missing scope, gate off) reads as
  // an incident when there is nothing to act on. See autonomy_readiness.py's
  // `only_rate_limited` handling, which this mirrors.
  const onlyRateLimited =
    !decision.allowed &&
    decision.channel_authorized &&
    decision.blocked_by.length === 1 &&
    decision.blocked_by[0] === 'rate_limit_reached'

  const publishingState = decision.allowed
    ? 'Ready to publish'
    : onlyRateLimited
      ? 'Rate limit reached'
      : decision.channel_authorized
        ? 'Blocked'
        : 'Not authorized'
  const publishingTone = decision.allowed
    ? 'badge-healthy'
    : onlyRateLimited
      ? 'badge-info'
      : decision.channel_authorized
        ? 'badge-warn'
        : 'badge-neutral'

  return (
    <section className="section">
      <SectionHeader
        title="Autonomous publishing"
        description="Status only — the controls live on the Channel page."
      />
      <div className="card">
        <div className="detail-meta-row">
          <span className="detail-meta-label">Next scheduled publication</span>
          <span className="detail-meta-value">
            {nextSlot
              ? <LocalTime value={nextSlot.scheduled_for_utc} variant="relative" />
              : 'Nothing scheduled'}
          </span>
        </div>
        <div className="detail-meta-row">
          <span className="detail-meta-label">Publishing</span>
          <span className="detail-meta-value">
            <span className={`badge ${publishingTone}`}>{publishingState}</span>
          </span>
        </div>
        {onlyRateLimited && (
          <div className="detail-meta-row">
            <span className="detail-meta-label" />
            <span className="detail-meta-value text-sm text-secondary">
              {decision.publications_last_24h}/{decision.max_publications_per_24h} publications in the last 24h —
              resumes automatically once the rolling window clears
            </span>
          </div>
        )}
        <div className="detail-meta-row">
          <span className="detail-meta-label">Finished video waiting</span>
          <span className="detail-meta-value">
            {readySlot ? 'Yes — rendered and validated' : 'None right now'}
          </span>
        </div>
        <div className="detail-meta-row">
          <span className="detail-meta-label">Automation health</span>
          <span className="detail-meta-value">
            {policy.decision_automation_enabled && policy.production_automation_enabled
              ? 'Planning and producing normally'
              : policy.decision_automation_enabled
                ? 'Planning only — production is paused'
                : 'Paused'}
          </span>
        </div>
      </div>
    </section>
  )
}

export function Dashboard() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''
  const navigate = useNavigate()
  const [chartMetric, setChartMetric] = useState<ChartMetric>('views')

  const { channel, primaryAccount, isLoading: channelLoading } = useCurrentChannel(wid)
  const perf = useChannelPerformance(wid)
  const health = useHealth(wid)
  const reviews = useReviewQueue(wid)
  const exceptions = useExceptionQueue(wid)

  const publicationIds = useMemo(
    () => perf.items.map(i => i.publication.id),
    [perf.items],
  )
  const recs = useChannelRecommendations(wid, publicationIds)

  if (!wid) {
    return (
      <div className="page-body">
        <EmptyState
          icon="🏢"
          title="No workspace selected"
          description="Select a workspace from the sidebar to view its dashboard."
        />
      </div>
    )
  }

  if (perf.isLoading && perf.items.length === 0) {
    return <LoadingState message="Loading dashboard…" />
  }
  if (perf.error) {
    return <ErrorState error={perf.error} />
  }

  const channelName = primaryAccount?.display_name || channel?.name || 'Channel'
  const { kpis } = perf

  // ── Attention signals — only real, current conditions ────────────────────
  type Severity = 'error' | 'warn' | 'info'
  const attentionItems: { severity: Severity; text: string; to: string }[] = []
  if (health.data && health.data.overall_status !== 'ok' && health.data.overall_status !== 'healthy') {
    const isError = health.data.overall_status === 'unavailable' || health.data.overall_status === 'critical'
    attentionItems.push({
      severity: isError ? 'error' : 'warn',
      text: `System health: ${humanizeStatus(health.data.overall_status).toLowerCase()}`,
      to: `/workspaces/${wid}/health`,
    })
  }
  if (primaryAccount && RECOVERABLE_ACCOUNT_STATUSES.has(primaryAccount.status)) {
    const isError = primaryAccount.status === 'credential_invalid' || primaryAccount.status === 'disconnected'
    attentionItems.push({
      severity: isError ? 'error' : 'warn',
      text: `YouTube connection: ${humanizeStatus(primaryAccount.status).toLowerCase()}`,
      to: `/workspaces/${wid}/channel`,
    })
  }
  const exceptionCount = exceptions.data?.length ?? 0
  if (exceptionCount > 0) {
    attentionItems.push({
      severity: 'error',
      text: `${exceptionCount} exception${exceptionCount === 1 ? '' : 's'} requiring attention`,
      to: `/workspaces/${wid}/exceptions`,
    })
  }
  const reviewCount = reviews.data?.length ?? 0
  if (reviewCount > 0) {
    attentionItems.push({
      severity: 'info',
      text: `${reviewCount} item${reviewCount === 1 ? '' : 's'} pending review`,
      to: `/workspaces/${wid}/reviews`,
    })
  }

  // ── Chart data: one bar per video with data ───────────────────────────────
  const chartData: VideoBarDatum[] = perf.items
    .filter(i => i.analytics?.metrics?.[chartMetric] !== undefined)
    .map(i => ({
      label: i.publication.title.length > 18
        ? `${i.publication.title.slice(0, 16)}…`
        : i.publication.title,
      title: i.publication.title,
      value: i.analytics!.metrics[chartMetric],
    }))
  const activeMetric = CHART_METRICS.find(m => m.key === chartMetric)!

  // ── Recent videos ──────────────────────────────────────────────────────
  const recentVideos: VideoCardData[] = [...perf.items]
    .sort((a, b) => (b.publication.published_at ?? '').localeCompare(a.publication.published_at ?? ''))
    .slice(0, 4)
    .map(i => ({
      id: i.publication.id,
      title: i.publication.title,
      providerVideoId: i.publication.provider_video_id,
      visibility: i.publication.visibility,
      status: i.publication.status,
      publishedAt: i.publication.published_at,
      durationMs: i.publication.render_duration_ms,
      views: i.analytics?.metrics.views ?? null,
      avgViewPercentage: i.analytics?.metrics.average_view_percentage ?? null,
      topicTitle: i.publication.topic_title,
    }))

  // ── Content pipeline — real publications.status buckets only ─────────────
  // Earlier lifecycle stages (topic → script → production → render) are not
  // currently reachable through any workspace-scoped endpoint for every
  // topic (a confirmed backend gap — see Phase 17B report), so this section
  // is scoped to what publications.status can honestly represent.
  const statusCounts = perf.items.reduce<Record<string, number>>((acc, i) => {
    const s = i.publication.status
    acc[s] = (acc[s] ?? 0) + 1
    return acc
  }, {})
  const pipelineBuckets = [
    { label: 'On YouTube', count: statusCounts.published ?? 0 },
    {
      label: 'Publishing',
      count: (statusCounts.uploading ?? 0) + (statusCounts.uploaded ?? 0) + (statusCounts.scheduled ?? 0),
    },
    { label: 'Failed', count: statusCounts.failed ?? 0 },
    { label: 'Archived', count: statusCounts.deleted ?? 0 },
  ]

  return (
    <>
      <PageHeader
        title={channelLoading ? 'Dashboard' : channelName}
        subtitle={
          primaryAccount
            ? `YouTube · ${humanizeStatus(primaryAccount.status)}`
            : 'Channel command center'
        }
        actions={
          health.data && (
            <StatusBadge status={health.data.overall_status} />
          )
        }
      />

      <div className="page-body">
        {/* Attention — compact inline alerts, only when something genuinely needs it */}
        {attentionItems.length > 0 && (
          <div className="attention-strip">
            {attentionItems.map((item, i) => (
              <button
                key={i}
                className={`attention-item attention-${item.severity}`}
                onClick={() => navigate(item.to)}
                type="button"
              >
                <span className="attention-dot" aria-hidden="true" />
                <span className="attention-text">{item.text}</span>
                <span className="attention-arrow" aria-hidden="true">→</span>
              </button>
            ))}
          </div>
        )}

        {/* Primary KPIs */}
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
              sub={kpis.videosWithDataCount > 0 ? 'total across published videos' : undefined}
            />
            <MetricCard
              label="Avg % viewed"
              value={formatPercent(kpis.avgViewPercentage)}
              sub={kpis.avgViewPercentage !== null ? 'view-weighted across videos' : undefined}
            />
            <MetricCard
              label="Subscribers gained"
              value={formatCompact(kpis.totalSubscribersGained)}
              sub={kpis.videosWithDataCount > 0 ? 'attributed to these videos' : undefined}
            />
          </div>
        </section>

        <PublishingStatusStrip workspaceId={wid} channelId={channel?.id ?? ''} />

        {/* Channel performance */}
        <section className="section">
          <SectionHeader
            title="Channel performance"
            description="Latest observed value per video. Individual videos currently have only 1–2 analytics observations, so this compares videos rather than showing a trend line."
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
                description="Performance data will appear here once the channel's videos have been observed."
              />
            )}
          </div>
        </section>

        {/* Recent videos */}
        <section className="section">
          <SectionHeader title="Recent videos" />
          {recentVideos.length > 0 ? (
            <div className="video-grid">
              {recentVideos.map(v => (
                <VideoCard
                  key={v.id}
                  video={v}
                  onClick={() => navigate(`/workspaces/${wid}/content/${v.id}`)}
                />
              ))}
            </div>
          ) : (
            <EmptyState icon="🎬" title="No videos yet" />
          )}
        </section>

        {/* Learning snapshot */}
        <section className="section">
          <SectionHeader
            title="What the system is learning"
            description="Early signals from this channel's videos. Treat exploratory findings as questions to investigate, not conclusions."
            actions={
              recs.recommendations.length > 0 && (
                <button className="btn btn-ghost btn-sm" onClick={() => navigate(`/workspaces/${wid}/learn`)}>
                  View all →
                </button>
              )
            }
          />
          {recs.recommendations.length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
              {recs.recommendations.slice(0, 3).map(r => (
                <div key={r.id} className="card">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 'var(--sp-3)' }}>
                    <div>
                      <p style={{ fontWeight: 600, margin: 0 }}>{r.title}</p>
                      <p className="text-sm text-secondary" style={{ margin: '4px 0 0' }}>{r.explanation}</p>
                    </div>
                    <MaturityBadge maturity={r.recommendation_strength} />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              icon="💡"
              title="Not enough data yet"
              description="Insights will appear here once the system has observed enough video performance to find patterns."
            />
          )}
        </section>

        {/* Content pipeline */}
        <section className="section">
          <SectionHeader
            title="Content pipeline"
            description="Publishing-stage status for this channel's content."
          />
          <div className="metric-grid">
            {pipelineBuckets.map(b => (
              <MetricCard key={b.label} label={b.label} value={String(b.count)} />
            ))}
          </div>
        </section>
      </div>
    </>
  )
}
