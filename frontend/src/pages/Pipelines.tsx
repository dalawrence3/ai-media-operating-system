/* M14.4 — Pipeline Studio */

import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { StatusBadge } from '@/components/common/StatusBadge'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { usePipelines, usePipeline, usePipelineMutations } from '@/hooks/usePipeline'
import type { PipelineStageView, PipelineView } from '@/api/types'

const STAGES = [
  'research', 'script_generation', 'production_plan', 'narration',
  'captions', 'visual_intelligence', 'rendering', 'publishing',
  'analytics', 'learning',
] as const

const STAGE_LABELS: Record<string, string> = {
  research: 'Research',
  script_generation: 'Script',
  production_plan: 'Production Plan',
  narration: 'Narration',
  captions: 'Captions',
  visual_intelligence: 'Visual Intel',
  rendering: 'Rendering',
  publishing: 'Publishing',
  analytics: 'Analytics',
  learning: 'Learning',
}

function stageColor(status: string): string {
  const map: Record<string, string> = {
    completed: 'var(--stage-done)',
    running: 'var(--stage-running)',
    failed: 'var(--stage-failed)',
    blocked: 'var(--stage-blocked)',
    waiting_for_review: 'var(--stage-review)',
  }
  return map[status] ?? 'var(--stage-pending)'
}

function PipelineStageBar({ stages }: { stages: PipelineStageView[] }) {
  const stageMap = new Map(stages.map(s => [s.stage, s]))
  return (
    <div
      role="list"
      aria-label="Pipeline stages"
      style={{
        display: 'flex',
        gap: 3,
        marginTop: 'var(--sp-3)',
      }}
    >
      {STAGES.map(stageName => {
        const st = stageMap.get(stageName)
        const color = st ? stageColor(st.status) : 'var(--border)'
        return (
          <div
            key={stageName}
            role="listitem"
            title={`${STAGE_LABELS[stageName]}: ${st?.status ?? 'not started'}`}
            aria-label={`${STAGE_LABELS[stageName]}: ${st?.status ?? 'not started'}`}
            style={{
              flex: 1,
              height: 6,
              borderRadius: 3,
              background: color,
            }}
          />
        )
      })}
    </div>
  )
}

function PipelineDetail({ workspaceId, pipeline }: { workspaceId: string; pipeline: PipelineView }) {
  const detail = usePipeline(workspaceId, pipeline.id)
  const mut = usePipelineMutations(workspaceId)

  const p = detail.data ?? pipeline
  const stageMap = new Map(p.stages.map(s => [s.stage, s]))

  return (
    <div>
      {/* Header */}
      <div className="card mb-6">
        <div className="card-header">
          <div>
            <p className="font-mono text-xs text-muted">{p.id}</p>
            <div className="flex gap-2 mt-2 items-center">
              <StatusBadge status={p.status} />
              {p.current_stage && (
                <span className="text-sm text-secondary">Stage: <strong>{STAGE_LABELS[p.current_stage] ?? p.current_stage}</strong></span>
              )}
            </div>
          </div>
          <div className="flex gap-2 flex-wrap">
            {p.status === 'running' && (
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => mut.pause.mutate(p.id)}
                disabled={mut.pause.isPending}
              >Pause</button>
            )}
            {p.status === 'paused' && (
              <button
                className="btn btn-primary btn-sm"
                onClick={() => mut.resume.mutate(p.id)}
                disabled={mut.resume.isPending}
              >Resume</button>
            )}
            {p.status === 'failed' && (
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => mut.recover.mutate(p.id)}
                disabled={mut.recover.isPending}
              >Recover</button>
            )}
            {['running', 'paused', 'pending'].includes(p.status) && (
              <button
                className="btn btn-danger btn-sm"
                onClick={() => { if (confirm('Cancel this pipeline?')) mut.cancel.mutate(p.id) }}
                disabled={mut.cancel.isPending}
              >Cancel</button>
            )}
          </div>
        </div>

        <PipelineStageBar stages={p.stages} />

        {p.error_message && (
          <div className="error-state mt-4">⚠ {p.error_message}</div>
        )}
        {p.blocked_reason && (
          <div className="diagnostic-finding diagnostic-finding-warning mt-4">
            ⏸ {p.blocked_reason}
          </div>
        )}
      </div>

      {/* Stage detail */}
      <section className="section">
        <div className="section-header">
          <h3 className="section-title">Stage History</h3>
        </div>
        <div className="table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>Stage</th>
                <th>Status</th>
                <th>Attempt</th>
                <th>Artifact</th>
                <th>Duration</th>
                <th>Error</th>
                <th>Completed</th>
              </tr>
            </thead>
            <tbody>
              {STAGES.map(stageName => {
                const s = stageMap.get(stageName)
                if (!s) return (
                  <tr key={stageName} style={{ opacity: .4 }}>
                    <td>{STAGE_LABELS[stageName]}</td>
                    <td><span className="badge badge-neutral">Not started</span></td>
                    <td>—</td><td>—</td><td>—</td><td>—</td><td>—</td>
                  </tr>
                )
                return (
                  <tr key={stageName}>
                    <td className="font-600">{STAGE_LABELS[stageName]}</td>
                    <td>
                      <div className="flex gap-2 items-center">
                        <div
                          aria-hidden="true"
                          style={{ width: 8, height: 8, borderRadius: '50%', background: stageColor(s.status), flexShrink: 0 }}
                        />
                        <StatusBadge status={s.status} />
                      </div>
                    </td>
                    <td>{s.attempt_number}</td>
                    <td className="font-mono text-xs text-muted truncate" style={{ maxWidth: 100 }}>
                      {s.artifact_id ? `${s.artifact_type}:${s.artifact_id.slice(0,8)}` : '—'}
                    </td>
                    <td className="text-sm">
                      {s.duration_ms != null ? `${(s.duration_ms / 1000).toFixed(1)}s` : '—'}
                    </td>
                    <td className="text-xs text-muted truncate" style={{ maxWidth: 180 }}>
                      {s.error_message ?? '—'}
                    </td>
                    <td className="text-xs text-muted">
                      {s.completed_at?.slice(0,16).replace('T',' ') ?? '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* Execute current stage (if blocked by executor) */}
      {p.status === 'running' && p.current_stage && (
        <section className="section">
          <div className="section-header">
            <h3 className="section-title">Stage Execution</h3>
          </div>
          <div className="card">
            <p className="text-sm text-secondary mb-4">
              Execute the current stage (<strong>{STAGE_LABELS[p.current_stage] ?? p.current_stage}</strong>) through ApplicationService.
              Some stages require provider/live-gate setup — check diagnostics if blocked.
            </p>
            <button
              className="btn btn-primary btn-sm"
              onClick={() => mut.executeStage.mutate({ id: p.id, stage: p.current_stage! })}
              disabled={mut.executeStage.isPending}
            >
              {mut.executeStage.isPending ? 'Executing…' : `Execute ${STAGE_LABELS[p.current_stage] ?? p.current_stage}`}
            </button>
            {mut.executeStage.error && (
              <div className="error-state mt-4">
                {(mut.executeStage.error as Error).message}
              </div>
            )}
          </div>
        </section>
      )}
    </div>
  )
}

export function Pipelines() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('')

  const { data: pipelines, isLoading, error, refetch } = usePipelines(wid, statusFilter || undefined)

  if (!wid) return (
    <div className="page-body">
      <EmptyState icon="▶" title="No workspace selected" />
    </div>
  )

  const selected = pipelines?.find(p => p.id === selectedId)

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Pipelines</h1>
          <p className="page-subtitle">Content production pipeline studio</p>
        </div>
        <select
          className="field-select"
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          <option value="running">Running</option>
          <option value="pending">Pending</option>
          <option value="paused">Paused</option>
          <option value="failed">Failed</option>
          <option value="completed">Completed</option>
          <option value="blocked">Blocked</option>
        </select>
      </div>

      <div className="page-body">
        {isLoading ? <LoadingState message="Loading pipelines…" /> :
         error ? <ErrorState error={error} retry={refetch} /> :
         !pipelines?.length ? (
           <EmptyState
             icon="▶"
             title="No pipelines"
             description={statusFilter ? `No pipelines with status "${statusFilter}"` : 'No pipelines found in this workspace.'}
           />
         ) : (
           <div style={{ display: 'grid', gridTemplateColumns: selected ? '340px 1fr' : '1fr', gap: 'var(--sp-6)' }}>
             {/* List */}
             <div>
               {pipelines.map(p => (
                 <button
                   key={p.id}
                   onClick={() => setSelectedId(p.id === selectedId ? null : p.id)}
                   style={{
                     display: 'block',
                     width: '100%',
                     textAlign: 'left',
                     padding: 'var(--sp-4)',
                     background: p.id === selectedId ? 'var(--accent-subtle)' : 'var(--surface-card)',
                     border: `1px solid ${p.id === selectedId ? 'var(--accent)' : 'var(--border)'}`,
                     borderRadius: 'var(--radius-lg)',
                     marginBottom: 'var(--sp-3)',
                     cursor: 'pointer',
                     transition: 'all var(--transition-fast)',
                   }}
                   aria-pressed={p.id === selectedId}
                   aria-label={`Pipeline ${p.id.slice(0,8)}`}
                 >
                   <div className="flex items-center justify-between gap-2">
                     <span className="font-mono text-xs text-muted">{p.id.slice(0,12)}…</span>
                     <StatusBadge status={p.status} />
                   </div>
                   {p.current_stage && (
                     <p className="text-sm font-600 mt-2">{STAGE_LABELS[p.current_stage] ?? p.current_stage}</p>
                   )}
                   <PipelineStageBar stages={p.stages} />
                   <p className="text-xs text-muted mt-2">{p.created_at.slice(0,10)}</p>
                 </button>
               ))}
             </div>

             {/* Detail */}
             {selected && (
               <div>
                 <PipelineDetail workspaceId={wid} pipeline={selected} />
               </div>
             )}
           </div>
         )}
      </div>
    </>
  )
}
