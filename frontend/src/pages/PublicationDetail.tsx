/* Phase 17B — Content detail. Consolidates lifecycle, production, and
   analytics information for a single video.

   The release-public flow below (state, handler, gating, modal) is
   safety-critical and unchanged from its prior implementation — only its
   visual presentation was touched, not its logic, conditionals, or copy. */

import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import {
  usePublication,
  usePublicationAnalytics,
  usePublicationVideoUrl,
  usePublicationVisualQuality,
} from '@/hooks/usePublications'
import { PageHeader } from '@/components/common/PageHeader'
import { SectionHeader } from '@/components/common/SectionHeader'
import { StatusBadge } from '@/components/common/StatusBadge'
import { MetricCard } from '@/components/common/MetricCard'
import { LocalTime } from '@/components/common/LocalTime'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { TechnicalDetails } from '@/components/common/TechnicalDetails'
import { VisualQualityPanel } from '@/components/common/VisualQualityPanel'
import { formatDurationMs } from '@/lib/format'
import { formatMetricValue, metricLabel, sortMetricEntries } from '@/lib/analyticsMetrics'

function MetaRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="detail-meta-row">
      <span className="detail-meta-label">{label}</span>
      <span className="detail-meta-value">{value}</span>
    </div>
  )
}

export function PublicationDetail() {
  const { workspaceId, publicationId } = useParams<{
    workspaceId: string
    publicationId: string
  }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const pubId = publicationId ? parseInt(publicationId, 10) : null
  const { data: pub, isLoading: pubLoading, error: pubError } = usePublication(workspaceId ?? '', pubId)
  const { data: analytics, isLoading: analyticsLoading } = usePublicationAnalytics(workspaceId ?? '', pubId)
  const { blobUrl, loading: videoLoading, error: videoError } = usePublicationVideoUrl(workspaceId ?? '', pubId)
  const { data: visualQuality, isLoading: visualLoading } = usePublicationVisualQuality(workspaceId ?? '', pubId)

  const [showReleaseModal, setShowReleaseModal] = useState(false)
  const [releasing, setReleasing] = useState(false)
  const [releaseError, setReleaseError] = useState<string | null>(null)
  const [releaseSuccess, setReleaseSuccess] = useState(false)

  async function handleConfirmRelease() {
    if (!workspaceId || pubId === null) return
    setReleasing(true)
    setReleaseError(null)
    try {
      await api.releasePublic(workspaceId, pubId)
      setReleaseSuccess(true)
      setShowReleaseModal(false)
      queryClient.invalidateQueries({ queryKey: ['publication', workspaceId, pubId] })
    } catch (err) {
      setReleaseError((err as Error).message ?? 'Release failed')
    } finally {
      setReleasing(false)
    }
  }

  if (pubLoading) {
    return <LoadingState message="Loading video…" />
  }

  if (pubError || !pub) {
    return (
      <div className="page-body">
        <button className="btn btn-ghost btn-sm" onClick={() => navigate(`/workspaces/${workspaceId}/content`)}>
          ← Content
        </button>
        <ErrorState error={pubError ?? new Error('Video not found.')} />
      </div>
    )
  }

  return (
    <>
      <div style={{ padding: 'var(--sp-4) var(--sp-8) 0' }}>
        <button className="btn btn-ghost btn-sm" onClick={() => navigate(`/workspaces/${workspaceId}/content`)}>
          ← Content
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
          gridTemplateColumns: '1fr 340px',
          gap: 'var(--sp-6)',
          alignItems: 'start',
        }}
      >
        {/* Video player + description */}
        <div>
          <div className="detail-video-frame">
            {videoLoading && <p className="text-sm" style={{ color: '#aaa' }}>Loading video…</p>}
            {videoError && !videoLoading && (
              <p className="text-sm" style={{ color: 'var(--status-error)', padding: 'var(--sp-4)' }}>
                Video unavailable: {videoError}
              </p>
            )}
            {blobUrl && !videoLoading && (
              <video
                controls
                src={blobUrl}
                style={{ maxHeight: '520px', maxWidth: '100%', display: 'block' }}
                data-testid="publication-video"
              />
            )}
          </div>

          {pub.description && (
            <p className="text-sm text-secondary" style={{ marginTop: 'var(--sp-4)' }}>
              {pub.description}
            </p>
          )}

          {pub.tags && pub.tags.length > 0 && (
            <div style={{ marginTop: 'var(--sp-3)', display: 'flex', flexWrap: 'wrap', gap: 'var(--sp-2)' }}>
              {pub.tags.map(tag => (
                <span key={tag} className="tag">#{tag}</span>
              ))}
            </div>
          )}

          {/* Visual quality (Phase 18E) */}
          <section className="section" style={{ marginTop: 'var(--sp-6)' }}>
            <SectionHeader title="Visual quality" />
            {visualLoading && <p className="text-sm text-secondary">Loading…</p>}
            {!visualLoading && visualQuality && <VisualQualityPanel data={visualQuality} />}
          </section>

          {/* Analytics */}
          <section className="section" style={{ marginTop: 'var(--sp-6)' }}>
            <SectionHeader
              title="Performance"
              actions={
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => navigate(`/workspaces/${workspaceId}/analytics/${pubId}`)}
                >
                  View analytics →
                </button>
              }
            />
            {analyticsLoading && <p className="text-sm text-secondary">Loading…</p>}
            {!analyticsLoading && analytics && (
              analytics.snapshot_id === null ? (
                <div className="card">
                  <p className="text-sm text-secondary" style={{ margin: 0 }}>
                    No analytics data yet. This video will be observed automatically.
                  </p>
                </div>
              ) : (
                <>
                  <div className="metric-grid">
                    {sortMetricEntries(analytics.metrics).map(([name, value]) => (
                      <MetricCard
                        key={name}
                        label={metricLabel(name)}
                        value={formatMetricValue(name, value)}
                      />
                    ))}
                  </div>
                  <div className="card" style={{ marginTop: 'var(--sp-4)' }}>
                    <MetaRow
                      label="Observed"
                      value={<LocalTime value={analytics.snapshot_ingested_at} variant="relative" />}
                    />
                    <MetaRow
                      label="Retention data"
                      value={
                        analytics.retention_point_count === 0
                          ? 'Not available for this video yet'
                          : `${analytics.retention_point_count} points`
                      }
                    />
                  </div>
                </>
              )
            )}
          </section>
        </div>

        {/* Sidebar */}
        <div>
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
          </div>

          <TechnicalDetails summary="Render & lineage details">
            <div className="detail-meta-list">
              <MetaRow label="Resolution" value={pub.render_width && pub.render_height ? `${pub.render_width}×${pub.render_height}` : '—'} />
              <MetaRow label="FPS" value={pub.render_fps ?? '—'} />
              <MetaRow label="Render status" value={pub.render_status ?? '—'} />
              <MetaRow label="Provider" value={pub.provider} />
              <MetaRow label="Created" value={<LocalTime value={pub.created_at} />} />
            </div>
          </TechnicalDetails>

          {/* Release action — safety-critical; logic/copy unchanged from prior implementation */}
          <div className="card" style={{ marginTop: 'var(--sp-3)' }}>
            <h3 className="detail-meta-label" style={{ marginBottom: 'var(--sp-2)' }}>Release</h3>

            {(() => {
              const isAlreadyPublic = releaseSuccess || pub.visibility === 'public'
              const canRelease =
                pub.release_eligible &&
                pub.release_enabled &&
                pub.release_scope_granted &&
                !isAlreadyPublic

              let helperText: React.ReactNode = null
              if (isAlreadyPublic) {
                helperText = (
                  <p style={{ fontSize: '0.875rem', color: 'var(--color-success, #16a34a)', margin: '0 0 12px' }}>
                    Released publicly.
                  </p>
                )
              } else if (!pub.release_enabled) {
                helperText = (
                  <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', margin: '0 0 12px' }}>
                    Release control not enabled. Set <code>ACE_RELEASE_PUBLIC_ENABLED=true</code>{' '}
                    and <code>ACE_PUBLISHING_LIVE_ENABLED=true</code>.
                  </p>
                )
              } else if (!pub.release_scope_granted) {
                helperText = (
                  <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', margin: '0 0 12px' }}>
                    YouTube release permission must be granted.{' '}
                    Run the <code>upgrade-release</code> OAuth flow to add{' '}
                    <code>youtube.force-ssl</code> scope.
                  </p>
                )
              } else if (!pub.release_eligible) {
                helperText = (
                  <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', margin: '0 0 12px' }}>
                    Publication must be in <code>published</code> status with <code>private</code>{' '}
                    visibility, a YouTube video ID, and an assigned platform account.
                  </p>
                )
              }

              return (
                <>
                  {helperText}

                  {releaseError && (
                    <p style={{ fontSize: '0.8rem', color: 'var(--color-error, #dc2626)', margin: '0 0 12px' }}>
                      {releaseError}
                    </p>
                  )}

                  <button
                    disabled={releasing || !canRelease}
                    onClick={() => { setReleaseError(null); setShowReleaseModal(true) }}
                    title={
                      isAlreadyPublic
                        ? 'Already released publicly'
                        : !pub.release_enabled
                        ? 'Release control not enabled'
                        : !pub.release_scope_granted
                        ? 'YouTube release permission must be granted'
                        : !pub.release_eligible
                        ? 'Publication not in a releasable state'
                        : 'Release this video publicly on YouTube'
                    }
                    style={{
                      width: '100%',
                      padding: '10px',
                      borderRadius: '6px',
                      border: '1px solid var(--border-color)',
                      background: canRelease
                        ? 'var(--color-primary, #2563eb)'
                        : 'var(--bg-tertiary, var(--bg-secondary))',
                      color: canRelease ? '#fff' : 'var(--text-secondary)',
                      fontSize: '0.875rem',
                      fontWeight: 600,
                      cursor: canRelease && !releasing ? 'pointer' : 'not-allowed',
                      opacity: releasing ? 0.7 : 1,
                    }}
                  >
                    {releasing ? 'Releasing…' : 'Release Publicly'}
                  </button>
                </>
              )
            })()}
          </div>

          {/* Confirmation modal */}
          {showReleaseModal && (
            <div
              role="dialog"
              aria-modal="true"
              aria-labelledby="release-modal-title"
              style={{
                position: 'fixed',
                inset: 0,
                background: 'rgba(0,0,0,0.5)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                zIndex: 1000,
              }}
            >
              <div
                style={{
                  background: 'var(--bg-primary, #fff)',
                  border: '1px solid var(--border-color)',
                  borderRadius: '8px',
                  padding: '24px',
                  maxWidth: '420px',
                  width: '90%',
                }}
              >
                <h2
                  id="release-modal-title"
                  style={{ margin: '0 0 12px', fontSize: '1rem', fontWeight: 700 }}
                >
                  Release Video Publicly?
                </h2>
                <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', margin: '0 0 20px' }}>
                  This will change <strong>{pub.title}</strong> from private to public on YouTube.
                  This action cannot be undone from this interface.
                </p>
                {releaseError && (
                  <p style={{ fontSize: '0.8rem', color: 'var(--color-error, #dc2626)', margin: '0 0 12px' }}>
                    {releaseError}
                  </p>
                )}
                <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                  <button
                    onClick={() => { setShowReleaseModal(false); setReleaseError(null) }}
                    disabled={releasing}
                    style={{
                      padding: '8px 16px',
                      borderRadius: '6px',
                      border: '1px solid var(--border-color)',
                      background: 'var(--bg-secondary)',
                      cursor: releasing ? 'not-allowed' : 'pointer',
                      fontSize: '0.875rem',
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleConfirmRelease}
                    disabled={releasing}
                    style={{
                      padding: '8px 16px',
                      borderRadius: '6px',
                      border: 'none',
                      background: 'var(--color-primary, #2563eb)',
                      color: '#fff',
                      cursor: releasing ? 'not-allowed' : 'pointer',
                      fontSize: '0.875rem',
                      fontWeight: 600,
                      opacity: releasing ? 0.7 : 1,
                    }}
                  >
                    {releasing ? 'Releasing…' : 'Confirm Release'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
