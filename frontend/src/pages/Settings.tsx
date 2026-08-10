/* M14.8 — Settings — backend-supported configuration only, no secrets */

import { useParams } from 'react-router-dom'
import { useState } from 'react'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { StatusBadge } from '@/components/common/StatusBadge'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

const SECTIONS = ['Workspace', 'Automation', 'Costs', 'Config'] as const

export function Settings() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''
  const [activeSection, setActiveSection] = useState<typeof SECTIONS[number]>('Workspace')

  const { data: config, isLoading: configLoading, error: configError, refetch: configRefetch } = useQuery({
    queryKey: ['config', wid],
    queryFn: () => api.getEffectiveConfig(wid),
    enabled: !!wid,
  })

  const { data: workspace, isLoading: wsLoading, error: wsError, refetch: wsRefetch } = useQuery({
    queryKey: ['workspace', wid],
    queryFn: () => api.getWorkspaceSummary(wid),
    enabled: !!wid,
  })

  if (!wid) return (
    <div className="page-body">
      <EmptyState icon="🔧" title="No workspace selected" />
    </div>
  )

  const sectionLoading = activeSection === 'Config' ? configLoading : wsLoading
  const sectionError = activeSection === 'Config' ? configError : wsError
  const sectionRefetch = activeSection === 'Config' ? configRefetch : wsRefetch

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
          {sectionLoading ? <LoadingState /> :
           sectionError ? <ErrorState error={sectionError} retry={sectionRefetch} /> : (

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

            ) : activeSection === 'Workspace' ? (
              <div className="card">
                <h2 className="card-title mb-4">Workspace</h2>
                {workspace && (
                  <div className="flex flex-col gap-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-secondary">Name</span>
                      <span className="font-600">{workspace.name}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-secondary">Slug</span>
                      <span className="font-mono text-sm">{workspace.slug}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-secondary">Status</span>
                      <StatusBadge status={workspace.status} />
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-secondary">Automation</span>
                      <span className="font-mono text-sm">{workspace.automation_level}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-secondary">Channels</span>
                      <span>{workspace.channel_count}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-secondary">Active Pipelines</span>
                      <span>{workspace.active_pipeline_count}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-secondary">ID</span>
                      <span className="font-mono text-xs text-muted">{wid}</span>
                    </div>
                  </div>
                )}
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
                {activeSection === 'Automation' && (
                  <div className="diagnostic-finding diagnostic-finding-info mt-3">
                    <span>
                      Automation level controls whether pipelines advance automatically.
                      Current level is visible per-channel under Channels → Channel Detail → Strategy Profile.
                      Set via: <code>ace policy set-automation-level &lt;workspace&gt; &lt;level&gt;</code>
                    </span>
                  </div>
                )}
                {activeSection === 'Costs' && (
                  <div className="diagnostic-finding diagnostic-finding-info mt-3">
                    <span>
                      Budget and cost summaries are visible under the Dashboard cost tiles.
                      Limits are configured via: <code>ace budget set &lt;workspace&gt;</code>
                    </span>
                  </div>
                )}
              </div>
            )
          )}
        </div>
      </div>
    </>
  )
}
