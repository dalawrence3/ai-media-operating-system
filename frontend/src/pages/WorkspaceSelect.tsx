/* Landing page when no workspace is selected */

import { useNavigate } from 'react-router-dom'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { StatusBadge } from '@/components/common/StatusBadge'
import { useWorkspaces } from '@/hooks/useWorkspace'

export function WorkspaceSelect() {
  const navigate = useNavigate()
  const { data: workspaces, isLoading, error, refetch } = useWorkspaces()

  if (isLoading) return <LoadingState message="Loading workspaces…" />
  if (error) return (
    <div style={{ padding: 'var(--sp-8)' }}>
      <ErrorState error={error} retry={refetch} />
    </div>
  )

  return (
    <div style={{ padding: 'var(--sp-12) var(--sp-8)', maxWidth: 600, margin: '0 auto' }}>
      <div className="dev-auth-banner" style={{ marginBottom: 'var(--sp-6)', borderRadius: 'var(--radius-md)' }}>
        ⚠ DEV MODE — Development authentication active
      </div>

      <h1 style={{ fontSize: 'var(--font-size-2xl)', fontWeight: 700, marginBottom: 'var(--sp-2)' }}>
        AI Media Operating System
      </h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: 'var(--sp-8)' }}>
        Studio Dashboard — Select a workspace to begin
      </p>

      {!workspaces?.length ? (
        <EmptyState
          icon="🏢"
          title="No workspaces found"
          description="Create a workspace using the Control Plane CLI: ace cp workspace create"
        />
      ) : (
        <div>
          {workspaces.map(w => (
            <button
              key={w.id}
              onClick={() => void navigate(`/workspaces/${w.id}/dashboard`)}
              style={{
                display: 'block',
                width: '100%',
                textAlign: 'left',
                padding: 'var(--sp-5)',
                background: 'var(--surface-card)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-lg)',
                marginBottom: 'var(--sp-3)',
                cursor: 'pointer',
                transition: 'border-color var(--transition-fast)',
              }}
              onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--accent)')}
              onMouseLeave={e => (e.currentTarget.style.borderColor = 'var(--border)')}
              aria-label={`Open workspace ${w.name}`}
            >
              <div className="flex items-center justify-between">
                <span style={{ fontSize: 'var(--font-size-lg)', fontWeight: 700 }}>{w.name}</span>
                <StatusBadge status={w.status} />
              </div>
              <p className="text-sm text-muted mt-2">/{w.slug}</p>
              {w.organization_id && (
                <p className="text-xs text-muted mt-1">Org: {w.organization_id.slice(0,12)}</p>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
