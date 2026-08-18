import { useNavigate, useParams } from 'react-router-dom'
import {
  usePublication,
  usePublicationAnalytics,
  usePublicationVideoUrl,
} from '@/hooks/usePublications'

function MetaCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        background: 'var(--surface-raised, var(--bg-secondary))',
        border: '1px solid var(--border-color)',
        borderRadius: '8px',
        padding: '16px',
        marginBottom: '12px',
      }}
    >
      <h3
        style={{
          margin: '0 0 12px',
          fontSize: '0.75rem',
          fontWeight: 700,
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
          color: 'var(--text-secondary)',
        }}
      >
        {title}
      </h3>
      {children}
    </div>
  )
}

function MetaRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'flex-start',
        gap: '8px',
        padding: '4px 0',
        fontSize: '0.875rem',
        borderBottom: '1px solid var(--border-color)',
      }}
    >
      <span style={{ color: 'var(--text-secondary)', flexShrink: 0 }}>{label}</span>
      <span style={{ fontWeight: 500, textAlign: 'right', wordBreak: 'break-all' }}>{value}</span>
    </div>
  )
}

function formatDuration(ms: number | null): string {
  if (ms === null) return '—'
  const s = Math.round(ms / 1000)
  const m = Math.floor(s / 60)
  const sec = s % 60
  return `${m}m ${sec}s`
}

function formatMetricValue(name: string, value: number): string {
  if (name === 'ctr') return `${(value * 100).toFixed(1)}%`
  if (name === 'average_view_duration') {
    const m = Math.floor(value / 60)
    const s = Math.round(value % 60)
    return `${m}m ${s}s`
  }
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return value.toFixed(value % 1 === 0 ? 0 : 2)
}

export function PublicationDetail() {
  const { workspaceId, publicationId } = useParams<{
    workspaceId: string
    publicationId: string
  }>()
  const navigate = useNavigate()

  const pubId = publicationId ? parseInt(publicationId, 10) : null
  const { data: pub, isLoading: pubLoading, error: pubError } = usePublication(workspaceId ?? '', pubId)
  const { data: analytics, isLoading: analyticsLoading } = usePublicationAnalytics(workspaceId ?? '', pubId)
  const { blobUrl, loading: videoLoading, error: videoError } = usePublicationVideoUrl(workspaceId ?? '', pubId)

  if (pubLoading) {
    return (
      <div className="page-body">
        <p style={{ color: 'var(--text-secondary)' }}>Loading publication…</p>
      </div>
    )
  }

  if (pubError || !pub) {
    return (
      <div className="page-body">
        <button
          onClick={() => navigate(`/workspaces/${workspaceId}/publishing`)}
          style={{ marginBottom: '16px', cursor: 'pointer', background: 'none', border: 'none', color: 'var(--text-secondary)', fontSize: '0.875rem', padding: 0 }}
        >
          ← Back to publications
        </button>
        <p style={{ color: 'var(--color-error, #dc2626)' }}>
          {pubError ? String(pubError) : 'Publication not found.'}
        </p>
      </div>
    )
  }

  const publishedAt = pub.published_at
    ? new Date(pub.published_at).toLocaleString('en-US', {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
      })
    : '—'

  return (
    <>
      <div className="page-header">
        <button
          onClick={() => navigate(`/workspaces/${workspaceId}/publishing`)}
          style={{ cursor: 'pointer', background: 'none', border: 'none', color: 'var(--text-secondary)', fontSize: '0.875rem', padding: 0, marginBottom: '8px' }}
        >
          ← Publications
        </button>
        <h1 className="page-title" style={{ marginTop: 0 }}>{pub.title}</h1>
        {pub.description && (
          <p style={{ color: 'var(--text-secondary)', margin: '4px 0 0', fontSize: '0.875rem' }}>
            {pub.description}
          </p>
        )}
      </div>

      <div
        className="page-body"
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 320px',
          gap: '24px',
          alignItems: 'start',
        }}
      >
        {/* Video player */}
        <div>
          <div
            style={{
              background: '#000',
              borderRadius: '8px',
              overflow: 'hidden',
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              minHeight: '240px',
              maxHeight: '520px',
            }}
          >
            {videoLoading && (
              <p style={{ color: '#aaa', fontSize: '0.875rem' }}>Loading video…</p>
            )}
            {videoError && !videoLoading && (
              <p style={{ color: '#f87171', fontSize: '0.875rem', padding: '16px' }}>
                Video unavailable: {videoError}
              </p>
            )}
            {blobUrl && !videoLoading && (
              <video
                controls
                src={blobUrl}
                style={{
                  maxHeight: '520px',
                  maxWidth: '100%',
                  display: 'block',
                }}
                data-testid="publication-video"
              />
            )}
          </div>

          {pub.tags && pub.tags.length > 0 && (
            <div style={{ marginTop: '12px', display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
              {pub.tags.map(tag => (
                <span
                  key={tag}
                  style={{
                    fontSize: '0.75rem',
                    padding: '2px 8px',
                    borderRadius: '9999px',
                    background: 'var(--bg-secondary)',
                    border: '1px solid var(--border-color)',
                    color: 'var(--text-secondary)',
                  }}
                >
                  #{tag}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Sidebar */}
        <div>
          {/* Render info */}
          <MetaCard title="Render">
            <MetaRow label="Resolution" value={pub.render_width && pub.render_height ? `${pub.render_width}×${pub.render_height}` : '—'} />
            <MetaRow label="Duration" value={formatDuration(pub.render_duration_ms)} />
            <MetaRow label="FPS" value={pub.render_fps ?? '—'} />
            <MetaRow label="Render status" value={pub.render_status ?? '—'} />
          </MetaCard>

          {/* Publication info */}
          <MetaCard title="Publication">
            <MetaRow label="Provider" value={pub.provider} />
            <MetaRow label="Visibility" value={pub.visibility} />
            <MetaRow label="Status" value={pub.status} />
            <MetaRow label="Published" value={publishedAt} />
            {pub.provider_url && (
              <MetaRow
                label="Video URL"
                value={
                  <a
                    href={pub.provider_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: 'var(--color-primary, #3b82f6)', fontSize: '0.75rem' }}
                  >
                    {pub.provider_video_id}
                  </a>
                }
              />
            )}
          </MetaCard>

          {/* Analytics */}
          <MetaCard title="Analytics">
            {analyticsLoading && (
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem' }}>Loading…</p>
            )}
            {!analyticsLoading && analytics && (
              <>
                {analytics.snapshot_id === null ? (
                  <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', margin: 0 }}>
                    No analytics data yet. Run <code>ace analytics ingest</code> to populate.
                  </p>
                ) : (
                  <>
                    {analytics.period_start && analytics.period_end && (
                      <MetaRow
                        label="Period"
                        value={`${analytics.period_start} – ${analytics.period_end}`}
                      />
                    )}
                    {Object.entries(analytics.metrics).map(([name, value]) => (
                      <MetaRow
                        key={name}
                        label={name.replace(/_/g, ' ')}
                        value={formatMetricValue(name, value)}
                      />
                    ))}
                    <MetaRow
                      label="Retention points"
                      value={analytics.retention_point_count === 0
                        ? 'None ingested'
                        : String(analytics.retention_point_count)}
                    />
                  </>
                )}
              </>
            )}
          </MetaCard>

          {/* Release action */}
          <div
            style={{
              padding: '16px',
              border: '1px solid var(--border-color)',
              borderRadius: '8px',
              background: 'var(--surface-raised, var(--bg-secondary))',
            }}
          >
            <h3
              style={{
                margin: '0 0 8px',
                fontSize: '0.75rem',
                fontWeight: 700,
                textTransform: 'uppercase',
                letterSpacing: '0.06em',
                color: 'var(--text-secondary)',
              }}
            >
              Release
            </h3>
            <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', margin: '0 0 12px' }}>
              Public release requires an OAuth scope upgrade (<code>youtube.force-ssl</code>).
              Run <code>ace oauth upgrade --release-scope</code> to unlock.
            </p>
            <button
              disabled
              title="Public release is not yet available — OAuth scope upgrade required"
              style={{
                width: '100%',
                padding: '10px',
                borderRadius: '6px',
                border: '1px solid var(--border-color)',
                background: 'var(--bg-tertiary, var(--bg-secondary))',
                color: 'var(--text-secondary)',
                fontSize: '0.875rem',
                fontWeight: 600,
                cursor: 'not-allowed',
                opacity: 0.5,
              }}
            >
              Release Publicly
            </button>
          </div>
        </div>
      </div>
    </>
  )
}
