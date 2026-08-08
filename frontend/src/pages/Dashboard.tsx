/* M14.4 — Dashboard: immediate operational picture for the workspace */

import { useParams } from 'react-router-dom'
import { StatTile } from '@/components/common/StatTile'
import { StatusBadge } from '@/components/common/StatusBadge'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import {
  useControlCenter,
  useHealth,
  useReviewQueue,
  useExceptionQueue,
  useCosts,
} from '@/hooks/useWorkspace'
import { usePipelines } from '@/hooks/usePipeline'

function fmt(n: number | undefined, decimals = 2): string {
  if (n === undefined) return '—'
  return n.toLocaleString(undefined, { maximumFractionDigits: decimals })
}

export function Dashboard() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''

  const health   = useHealth(wid)
  const cc       = useControlCenter(wid)
  const reviews  = useReviewQueue(wid)
  const exc      = useExceptionQueue(wid)
  const costs    = useCosts(wid)
  const pipelines = usePipelines(wid, 'running')

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

  if (health.isLoading) return <LoadingState message="Loading dashboard…" />
  if (health.error)   return <ErrorState error={health.error} retry={health.refetch} />

  const h = health.data!
  const reviewCount = reviews.data?.length ?? 0
  const excCount    = exc.data?.length ?? 0
  const runningCount = pipelines.data?.length ?? 0
  const totalUsd    = costs.data?.total_usd

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <p className="page-subtitle">Workspace operational overview</p>
        </div>
        <StatusBadge status={h.overall_status} />
      </div>

      <div className="page-body">
        {/* Overview stats */}
        <section className="section">
          <div className="stat-grid">
            <StatTile
              label="Reviews Pending"
              value={reviewCount}
              sub={reviewCount > 0 ? 'Require attention' : 'None pending'}
              accent={reviewCount > 0}
            />
            <StatTile
              label="Exceptions"
              value={excCount}
              sub={excCount > 0 ? 'Operational issues' : 'None active'}
              accent={excCount > 0}
            />
            <StatTile
              label="Running Pipelines"
              value={runningCount}
              sub="Currently executing"
            />
            <StatTile
              label="Dead Letters"
              value={h.dead_letter_count}
              sub="Event bus failures"
              accent={h.dead_letter_count > 0}
            />
            <StatTile
              label="Stuck Operations"
              value={h.stuck_operation_count}
              sub="Need recovery"
              accent={h.stuck_operation_count > 0}
            />
            <StatTile
              label="Monthly Spend"
              value={totalUsd !== undefined ? `$${fmt(totalUsd)}` : '—'}
              sub={costs.data?.warning_active ? '⚠ Budget warning' :
                   costs.data?.block_active   ? '🚫 Budget blocked' : 'Current period'}
              accent={costs.data?.warning_active || costs.data?.block_active}
            />
          </div>
        </section>

        {/* Health details */}
        <section className="section">
          <div className="section-header">
            <h2 className="section-title">System Health</h2>
          </div>
          <div className="card">
            <div className="flex gap-4 flex-wrap">
              <div className="flex flex-col gap-2">
                <span className="text-xs text-muted">Event Bus</span>
                <StatusBadge
                  status={h.event_bus_ok ? 'healthy' : 'error'}
                  label={h.event_bus_ok ? 'OK' : 'Issue'}
                />
              </div>
              <div className="flex flex-col gap-2">
                <span className="text-xs text-muted">Budget</span>
                <StatusBadge
                  status={h.budget_status}
                  label={h.budget_status}
                />
              </div>
              <div className="flex flex-col gap-2">
                <span className="text-xs text-muted">Overall</span>
                <StatusBadge status={h.overall_status} />
              </div>
            </div>
          </div>
        </section>

        {/* Review queue preview */}
        {reviewCount > 0 && (
          <section className="section">
            <div className="section-header">
              <h2 className="section-title">Pending Reviews</h2>
            </div>
            <div className="table-wrapper">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>ID</th>
                    <th>Description</th>
                    <th>Status</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {(reviews.data ?? []).slice(0, 10).map(r => (
                    <tr key={`${r.item_type}:${r.item_id}`}>
                      <td><span className="tag">{r.item_type}</span></td>
                      <td className="font-mono text-xs truncate" style={{ maxWidth: 120 }}>{r.item_id}</td>
                      <td className="truncate" style={{ maxWidth: 260 }}>{r.description}</td>
                      <td><StatusBadge status={r.status} /></td>
                      <td className="text-xs text-muted">{r.created_at.slice(0, 10)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* Exception preview */}
        {excCount > 0 && (
          <section className="section">
            <div className="section-header">
              <h2 className="section-title">Active Exceptions</h2>
            </div>
            <div className="table-wrapper">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Severity</th>
                    <th>Description</th>
                    <th>When</th>
                  </tr>
                </thead>
                <tbody>
                  {(exc.data ?? []).slice(0, 10).map((e, i) => (
                    <tr key={i}>
                      <td><span className="tag">{e.exception_type}</span></td>
                      <td><StatusBadge status={e.severity} /></td>
                      <td className="truncate" style={{ maxWidth: 300 }}>{e.description}</td>
                      <td className="text-xs text-muted">{e.occurred_at.slice(0, 16).replace('T', ' ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* Running pipelines */}
        {runningCount > 0 && (
          <section className="section">
            <div className="section-header">
              <h2 className="section-title">Running Pipelines</h2>
            </div>
            <div className="table-wrapper">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Stage</th>
                    <th>Status</th>
                    <th>Actor</th>
                    <th>Started</th>
                  </tr>
                </thead>
                <tbody>
                  {(pipelines.data ?? []).map(p => (
                    <tr key={p.id}>
                      <td className="font-mono text-xs truncate" style={{ maxWidth: 120 }}>{p.id.slice(0, 12)}…</td>
                      <td>{p.current_stage ?? '—'}</td>
                      <td><StatusBadge status={p.status} /></td>
                      <td className="text-sm text-muted">{p.actor}</td>
                      <td className="text-xs text-muted">{p.created_at.slice(0, 16).replace('T', ' ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* Control-center data — additional context */}
        {cc.data && (
          <section className="section">
            <div className="section-header">
              <h2 className="section-title">Control Center</h2>
            </div>
            <div className="card">
              <pre className="font-mono text-xs text-secondary" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                {JSON.stringify(cc.data, null, 2)}
              </pre>
            </div>
          </section>
        )}
      </div>
    </>
  )
}
