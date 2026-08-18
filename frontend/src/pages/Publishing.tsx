import { useNavigate, useParams } from 'react-router-dom'
import { usePublications } from '@/hooks/usePublications'
import { UnavailableState } from '@/components/common/UnavailableState'
import type { PublicationListItem } from '@/api/types'

function VisibilityBadge({ visibility }: { visibility: string }) {
  const color = visibility === 'public' ? 'var(--color-success, #16a34a)' : 'var(--text-secondary)'
  return (
    <span
      style={{
        fontSize: '0.75rem',
        fontWeight: 600,
        padding: '2px 8px',
        borderRadius: '9999px',
        border: `1px solid ${color}`,
        color,
        textTransform: 'capitalize',
      }}
    >
      {visibility}
    </span>
  )
}

function StatusBadge({ status }: { status: string }) {
  const color =
    status === 'published' ? 'var(--color-success, #16a34a)'
    : status === 'scheduled' ? 'var(--color-warning, #ca8a04)'
    : 'var(--text-secondary)'
  return (
    <span
      style={{
        fontSize: '0.75rem',
        fontWeight: 600,
        padding: '2px 8px',
        borderRadius: '9999px',
        background: `${color}22`,
        color,
        textTransform: 'capitalize',
      }}
    >
      {status}
    </span>
  )
}

function PublicationRow({ pub, onClick }: { pub: PublicationListItem; onClick: () => void }) {
  const publishedAt = pub.published_at
    ? new Date(pub.published_at).toLocaleDateString('en-US', {
        year: 'numeric', month: 'short', day: 'numeric',
      })
    : '—'

  return (
    <tr
      onClick={onClick}
      style={{ cursor: 'pointer' }}
      className="table-row-hover"
    >
      <td style={{ padding: '12px 16px', fontWeight: 500 }}>{pub.title}</td>
      <td style={{ padding: '12px 16px' }}>{pub.provider}</td>
      <td style={{ padding: '12px 16px' }}>
        <VisibilityBadge visibility={pub.visibility} />
      </td>
      <td style={{ padding: '12px 16px' }}>
        <StatusBadge status={pub.status} />
      </td>
      <td style={{ padding: '12px 16px', color: 'var(--text-secondary)', fontSize: '0.875rem' }}>
        {publishedAt}
      </td>
      <td style={{ padding: '12px 16px', color: 'var(--text-secondary)', fontSize: '0.875rem' }}>
        {pub.provider_video_id ?? '—'}
      </td>
    </tr>
  )
}

export function Publishing() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const { data: publications, isLoading, error } = usePublications(workspaceId ?? '')

  if (!workspaceId) {
    return (
      <div className="page-body">
        <UnavailableState
          title="No workspace selected"
          description="Select a workspace to view publications."
          reason="no_data"
        />
      </div>
    )
  }

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">Publishing</h1>
        <p style={{ color: 'var(--text-secondary)', margin: '4px 0 0', fontSize: '0.875rem' }}>
          Review and manage published videos. Private visibility only — public release is pending OAuth scope upgrade.
        </p>
      </div>

      <div className="page-body">
        {isLoading && (
          <p style={{ color: 'var(--text-secondary)' }}>Loading publications…</p>
        )}

        {error && (
          <UnavailableState
            title="Could not load publications"
            description={String(error)}
            reason="error"
          />
        )}

        {!isLoading && !error && publications?.length === 0 && (
          <UnavailableState
            title="No publications yet"
            description="Publications will appear here after a pipeline completes the publishing stage."
            reason="no_data"
          />
        )}

        {!isLoading && !error && publications && publications.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-color)' }}>
                  {['Title', 'Provider', 'Visibility', 'Status', 'Published', 'Video ID'].map(h => (
                    <th
                      key={h}
                      style={{
                        padding: '8px 16px',
                        textAlign: 'left',
                        fontSize: '0.75rem',
                        fontWeight: 600,
                        color: 'var(--text-secondary)',
                        textTransform: 'uppercase',
                        letterSpacing: '0.05em',
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {publications.map(pub => (
                  <PublicationRow
                    key={pub.id}
                    pub={pub}
                    onClick={() => navigate(`/workspaces/${workspaceId}/publishing/${pub.id}`)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
