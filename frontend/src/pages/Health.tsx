/* M14.8 — Health / Monitoring UI */

import { useParams } from 'react-router-dom'
import { StatusBadge } from '@/components/common/StatusBadge'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { StatTile } from '@/components/common/StatTile'
import { useHealth } from '@/hooks/useWorkspace'

export function Health() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''
  const { data: h, isLoading, error, refetch } = useHealth(wid)

  if (!wid) return (
    <div className="page-body">
      <EmptyState icon="❤" title="No workspace selected" />
    </div>
  )

  if (isLoading) return <LoadingState message="Loading health…" />
  if (error) return <ErrorState error={error} retry={refetch} />

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">System Health</h1>
          <p className="page-subtitle">Workspace health and monitoring</p>
        </div>
        <StatusBadge status={h!.overall_status} />
      </div>

      <div className="page-body">
        <section className="section">
          <div className="stat-grid">
            <StatTile label="Overall" value={h!.overall_status} />
            <StatTile
              label="Event Bus"
              value={h!.event_bus_ok ? '✓ OK' : '✗ Issue'}
              accent={!h!.event_bus_ok}
            />
            <StatTile label="Dead Letters" value={h!.dead_letter_count} accent={h!.dead_letter_count > 0} />
            <StatTile label="Stuck Operations" value={h!.stuck_operation_count} accent={h!.stuck_operation_count > 0} />
            <StatTile label="Active Pipelines" value={h!.active_pipeline_count} />
            <StatTile label="Budget" value={h!.budget_status} />
          </div>
        </section>

        {Object.keys(h!.details).length > 0 && (
          <section className="section">
            <div className="section-header">
              <h2 className="section-title">Detail</h2>
            </div>
            <div className="card">
              <pre className="font-mono text-xs text-secondary" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                {JSON.stringify(h!.details, null, 2)}
              </pre>
            </div>
          </section>
        )}
      </div>
    </>
  )
}
