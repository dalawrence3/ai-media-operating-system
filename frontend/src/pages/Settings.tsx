/* M14.8 — Settings — backend-supported configuration only, no secrets */

import { useParams } from 'react-router-dom'
import { useState } from 'react'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

const SECTIONS = ['Workspace', 'Automation', 'Costs', 'Config']

export function Settings() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''
  const [activeSection, setActiveSection] = useState('Workspace')

  const { data: config, isLoading, error, refetch } = useQuery({
    queryKey: ['config', wid],
    queryFn: () => api.getEffectiveConfig(wid),
    enabled: !!wid,
  })

  if (!wid) return (
    <div className="page-body">
      <EmptyState icon="🔧" title="No workspace selected" />
    </div>
  )

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-subtitle">Workspace configuration — no secrets exposed</p>
        </div>
      </div>

      <div className="page-body" style={{ display: 'grid', gridTemplateColumns: '180px 1fr', gap: 'var(--sp-6)' }}>
        {/* Section nav */}
        <nav aria-label="Settings sections">
          {SECTIONS.map(s => (
            <button
              key={s}
              onClick={() => setActiveSection(s)}
              className="nav-link"
              style={{
                background: s === activeSection ? 'var(--accent-subtle)' : 'transparent',
                color: s === activeSection ? 'var(--accent)' : 'var(--text-secondary)',
                border: `1px solid ${s === activeSection ? 'var(--accent)' : 'transparent'}`,
                width: '100%',
                justifyContent: 'flex-start',
                marginBottom: 'var(--sp-1)',
                borderRadius: 'var(--radius-md)',
                padding: '8px 12px',
              }}
              aria-current={s === activeSection ? 'page' : undefined}
            >
              {s}
            </button>
          ))}
        </nav>

        {/* Section content */}
        <div>
          {isLoading ? <LoadingState /> :
           error ? <ErrorState error={error} retry={refetch} /> : (

            activeSection === 'Config' ? (
              <div className="card">
                <h2 className="card-title mb-4">Effective Configuration</h2>
                <p className="text-sm text-secondary mb-4">
                  Resolved configuration for this workspace — secrets are never exposed.
                </p>
                <pre className="font-mono text-xs text-secondary" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                  {JSON.stringify(config, null, 2)}
                </pre>
              </div>
            ) : (
              <div className="card">
                <h2 className="card-title mb-4">{activeSection}</h2>
                <div className="diagnostic-finding diagnostic-finding-info">
                  <span>
                    {activeSection} settings are managed through the Control Plane CLI.
                    Read-only display of configured values will appear here once
                    backend configuration endpoints are available.
                  </span>
                </div>
              </div>
            )
          )}
        </div>
      </div>
    </>
  )
}
