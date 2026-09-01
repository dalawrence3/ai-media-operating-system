/* M14.4 — Pipeline Studio (Batch 2: topic management, artifact viewer, diagnostics) */

import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { StatusBadge } from '@/components/common/StatusBadge'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { Modal } from '@/components/common/Modal'
import { TechnicalDetails } from '@/components/common/TechnicalDetails'
import {
  usePipelines, usePipeline, usePipelineMutations,
  useStartPipeline, useStageArtifact, usePipelineDiagnostics,
} from '@/hooks/usePipeline'
import { useChannels } from '@/hooks/useChannel'
import { useTopics, useCreateTopic } from '@/hooks/useTopics'
import type { PipelineStageView, PipelineView, DiagnosticReport, StageArtifact } from '@/api/types'

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
      style={{ display: 'flex', gap: 3, marginTop: 'var(--sp-3)' }}
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
            style={{ flex: 1, height: 6, borderRadius: 3, background: color }}
          />
        )
      })}
    </div>
  )
}

// ── Start Pipeline Modal ─────────────────────────────────────────────────────

function StartPipelineModal({ workspaceId, open, onClose }: {
  workspaceId: string
  open: boolean
  onClose: () => void
}) {
  const [channelId, setChannelId] = useState('')
  const [endStage, setEndStage] = useState('learning')
  const [topicId, setTopicId] = useState<string>('')
  const [showNewTopic, setShowNewTopic] = useState(false)
  const [newTopicTitle, setNewTopicTitle] = useState('')
  const [newTopicAngle, setNewTopicAngle] = useState('')
  const [apiError, setApiError] = useState<string | null>(null)

  const { data: channels } = useChannels(workspaceId)
  const { data: topics, isLoading: topicsLoading } = useTopics(workspaceId)
  const startPipeline = useStartPipeline(workspaceId)
  const createTopic = useCreateTopic(workspaceId)

  function generateKey() {
    return `ui-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
  }

  async function handleCreateTopic() {
    if (!newTopicTitle.trim()) return
    try {
      const topic = await createTopic.mutateAsync({ title: newTopicTitle.trim(), angle: newTopicAngle.trim() })
      setTopicId(String(topic.id))
      setNewTopicTitle('')
      setNewTopicAngle('')
      setShowNewTopic(false)
    } catch (err) {
      setApiError((err as Error).message)
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setApiError(null)
    try {
      await startPipeline.mutateAsync({
        channel_id: channelId,
        idempotency_key: generateKey(),
        end_stage: endStage,
        topic_id: topicId ? Number(topicId) : null,
      })
      reset()
      onClose()
    } catch (err) {
      setApiError((err as Error).message)
    }
  }

  function reset() {
    setChannelId(''); setEndStage('learning'); setTopicId('')
    setShowNewTopic(false); setNewTopicTitle(''); setNewTopicAngle(''); setApiError(null)
  }

  function handleClose() { reset(); onClose() }

  const isValid = channelId.length > 0

  return (
    <Modal
      open={open}
      title="Start Pipeline"
      onClose={handleClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={handleClose} disabled={startPipeline.isPending}>
            Cancel
          </button>
          <button
            type="submit"
            form="start-pipeline-form"
            className="btn btn-primary"
            disabled={!isValid || startPipeline.isPending}
          >{startPipeline.isPending ? 'Starting…' : 'Start Pipeline'}</button>
        </>
      }
    >
      <form id="start-pipeline-form" onSubmit={handleSubmit}>
        <div className="form-group">
          <label htmlFor="pipeline-channel" className="form-label">Channel <span aria-hidden="true">*</span></label>
          <select
            id="pipeline-channel"
            className="field-select"
            value={channelId}
            onChange={e => setChannelId(e.target.value)}
            required
            aria-required="true"
          >
            <option value="">Select a channel…</option>
            {channels?.map(ch => <option key={ch.id} value={ch.id}>{ch.name}</option>)}
          </select>
        </div>

        <div className="form-group">
          <label htmlFor="pipeline-topic" className="form-label">Topic</label>
          {topicsLoading ? (
            <p className="text-sm text-muted">Loading topics…</p>
          ) : (
            <select
              id="pipeline-topic"
              className="field-select"
              value={topicId}
              onChange={e => {
                if (e.target.value === '__new__') { setShowNewTopic(true); setTopicId('') }
                else { setTopicId(e.target.value); setShowNewTopic(false) }
              }}
            >
              <option value="">No topic (optional)</option>
              {topics?.map(t => <option key={t.id} value={String(t.id)}>{t.title}</option>)}
              <option value="__new__">+ Create new topic…</option>
            </select>
          )}
        </div>

        {showNewTopic && (
          <div className="card mb-4" style={{ padding: 'var(--sp-3)' }}>
            <p className="text-sm font-600 mb-3">New Topic</p>
            <div className="form-group">
              <label htmlFor="new-topic-title" className="form-label">Title <span aria-hidden="true">*</span></label>
              <input
                id="new-topic-title"
                type="text"
                className="field-input"
                value={newTopicTitle}
                onChange={e => setNewTopicTitle(e.target.value)}
                placeholder="Topic title"
                aria-label="New topic title"
              />
            </div>
            <div className="form-group">
              <label htmlFor="new-topic-angle" className="form-label">Angle</label>
              <input
                id="new-topic-angle"
                type="text"
                className="field-input"
                value={newTopicAngle}
                onChange={e => setNewTopicAngle(e.target.value)}
                placeholder="Optional angle / focus"
                aria-label="Topic angle"
              />
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleCreateTopic}
                disabled={!newTopicTitle.trim() || createTopic.isPending}
                aria-label="Save new topic"
              >{createTopic.isPending ? 'Saving…' : 'Save Topic'}</button>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => { setShowNewTopic(false); setNewTopicTitle(''); setNewTopicAngle('') }}
              >Discard</button>
            </div>
          </div>
        )}

        {topicId && !showNewTopic && (
          <div className="text-sm text-muted mb-4">
            Topic: <strong>{topics?.find(t => String(t.id) === topicId)?.title ?? `ID ${topicId}`}</strong>
          </div>
        )}

        <div className="form-group">
          <label htmlFor="pipeline-end-stage" className="form-label">Run through stage</label>
          <select
            id="pipeline-end-stage"
            className="field-select"
            value={endStage}
            onChange={e => setEndStage(e.target.value)}
          >
            {STAGES.map(s => <option key={s} value={s}>{STAGE_LABELS[s]}</option>)}
          </select>
        </div>

        {apiError && <div role="alert" className="form-error">{apiError}</div>}
      </form>
    </Modal>
  )
}

// ── Artifact viewer ──────────────────────────────────────────────────────────

function ArtifactPanel({ artifact }: { artifact: StageArtifact }) {
  if (!artifact.resolved) {
    const reasonLabels: Record<string, string> = {
      no_artifact: 'No artifact produced yet',
      type_binary: 'Binary artifact (not inspectable here)',
      type_secret: 'Credential artifact (not shown)',
      type_not_inspectable: 'Artifact type not yet inspectable',
      content_unavailable: 'Content temporarily unavailable',
    }
    return (
      <div className="text-sm text-muted" role="status">
        {reasonLabels[artifact.reason ?? ''] ?? 'Artifact unavailable'}
      </div>
    )
  }
  const summary = summarizeArtifactContent(artifact.content)
  return (
    <div aria-label="Artifact content">
      <p className="text-xs text-muted mb-2">Type: <code>{artifact.content_type}</code></p>
      {summary.length > 0 ? (
        <div className="detail-meta-list">
          {summary.map(([key, value]) => (
            <div key={key} className="detail-meta-row">
              <span className="detail-meta-label">{key.replace(/_/g, ' ')}</span>
              <span className="detail-meta-value">{value}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted">No summarizable fields for this artifact.</p>
      )}
      {artifact.truncated && (
        <p className="text-xs text-muted mt-1">Content truncated for display</p>
      )}
      <TechnicalDetails summary="Raw artifact JSON" data={artifact.content} />
    </div>
  )
}

/** Top-level primitive fields only — enough to orient without dumping the
    whole structure. Nested objects/arrays stay behind the raw-JSON toggle. */
function summarizeArtifactContent(content: Record<string, unknown> | undefined): Array<[string, string]> {
  if (!content) return []
  const entries: Array<[string, string]> = []
  for (const [key, value] of Object.entries(content)) {
    if (value === null || value === undefined) continue
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      const str = String(value)
      entries.push([key, str.length > 160 ? `${str.slice(0, 160)}…` : str])
    }
  }
  return entries
}

// ── Diagnostics panel ────────────────────────────────────────────────────────

function DiagnosticsPanel({ report }: { report: DiagnosticReport }) {
  if (!report.findings.length) {
    return <p className="text-sm text-muted">No diagnostic findings.</p>
  }
  const severityColor: Record<string, string> = {
    error: 'var(--color-danger)',
    warning: 'var(--color-warning)',
    info: 'var(--color-info)',
  }
  return (
    <div>
      {report.findings.map((f, i) => (
        <div
          key={i}
          className="diagnostic-finding"
          style={{ borderLeft: `3px solid ${severityColor[f.severity] ?? 'var(--border)'}`, paddingLeft: 'var(--sp-3)', marginBottom: 'var(--sp-2)' }}
        >
          <p className="text-sm font-600">{f.message}</p>
          <p className="text-xs text-muted">{f.category}</p>
        </div>
      ))}
    </div>
  )
}

// ── Stage drilldown row ───────────────────────────────────────────────────────

function StageRow({
  workspaceId,
  pipelineId,
  stageName,
  stage,
  isCurrentStage,
  onAdvance,
  advancePending,
}: {
  workspaceId: string
  pipelineId: string
  stageName: string
  stage: PipelineStageView | undefined
  isCurrentStage: boolean
  onAdvance: (stage: string) => void
  advancePending: boolean
}) {
  const [expanded, setExpanded] = useState(false)

  const hasArtifact = !!stage?.artifact_id
  const artifactQuery = useStageArtifact(workspaceId, pipelineId, stageName, expanded && hasArtifact)
  const diagQuery = usePipelineDiagnostics(workspaceId, pipelineId, stageName, expanded)

  if (!stage) {
    return (
      <tr key={stageName} style={{ opacity: .4 }}>
        <td>{STAGE_LABELS[stageName]}</td>
        <td><span className="badge badge-neutral">Not started</span></td>
        <td>—</td><td>—</td><td>—</td><td>—</td><td>—</td>
      </tr>
    )
  }

  return (
    <>
      <tr key={stageName} style={{ background: isCurrentStage ? 'var(--accent-subtle)' : undefined }}>
        <td className="font-600">
          <button
            className="btn-link"
            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontWeight: 600 }}
            onClick={() => setExpanded(v => !v)}
            aria-expanded={expanded}
            aria-label={`${expanded ? 'Collapse' : 'Expand'} ${STAGE_LABELS[stageName]} details`}
          >
            {expanded ? '▾' : '▸'} {STAGE_LABELS[stageName]}
          </button>
        </td>
        <td>
          <div className="flex gap-2 items-center">
            <div
              aria-hidden="true"
              style={{ width: 8, height: 8, borderRadius: '50%', background: stageColor(stage.status), flexShrink: 0 }}
            />
            <StatusBadge status={stage.status} />
          </div>
        </td>
        <td>{stage.attempt_number}</td>
        <td className="font-mono text-xs text-muted truncate" style={{ maxWidth: 100 }}>
          {stage.artifact_id ? `${stage.artifact_type}:${stage.artifact_id.slice(0, 8)}` : '—'}
        </td>
        <td className="text-sm">
          {stage.duration_ms != null ? `${(stage.duration_ms / 1000).toFixed(1)}s` : '—'}
        </td>
        <td className="text-xs text-muted truncate" style={{ maxWidth: 180 }}>
          {stage.error_message ?? '—'}
        </td>
        <td className="text-xs text-muted">
          {stage.completed_at?.slice(0, 16).replace('T', ' ') ?? '—'}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={7} style={{ padding: 'var(--sp-3) var(--sp-4)', background: 'var(--surface-elevated)' }}>
            <div style={{ display: 'grid', gridTemplateColumns: hasArtifact ? '1fr 1fr' : '1fr', gap: 'var(--sp-4)' }}>
              {/* Artifact panel */}
              {hasArtifact && (
                <div>
                  <p className="text-xs font-600 mb-2">Artifact</p>
                  {artifactQuery.isLoading ? (
                    <p className="text-sm text-muted">Loading…</p>
                  ) : artifactQuery.error ? (
                    <p className="text-sm text-muted">Artifact unavailable</p>
                  ) : artifactQuery.data ? (
                    <ArtifactPanel artifact={artifactQuery.data} />
                  ) : null}
                </div>
              )}
              {/* Diagnostics */}
              <div>
                <p className="text-xs font-600 mb-2">Diagnostics</p>
                {diagQuery.isLoading ? (
                  <p className="text-sm text-muted" role="status">Loading diagnostics…</p>
                ) : diagQuery.error ? (
                  <p className="text-sm text-muted">Diagnostics unavailable</p>
                ) : diagQuery.data ? (
                  <DiagnosticsPanel report={diagQuery.data} />
                ) : null}
              </div>
            </div>
            {/* Manual advance */}
            {stage.status === 'waiting_for_review' && (
              <div className="mt-4">
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => onAdvance(stageName)}
                  disabled={advancePending}
                  aria-label={`Manually advance ${STAGE_LABELS[stageName]} stage`}
                >
                  {advancePending ? 'Advancing…' : `Advance ${STAGE_LABELS[stageName]}`}
                </button>
                <p className="text-xs text-muted mt-1">
                  Marks this stage complete and moves the pipeline forward.
                </p>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

// ── Pipeline detail ──────────────────────────────────────────────────────────

function PipelineDetail({ workspaceId, pipeline }: { workspaceId: string; pipeline: PipelineView }) {
  const detail = usePipeline(workspaceId, pipeline.id)
  const mut = usePipelineMutations(workspaceId)

  const p = detail.data ?? pipeline
  const stageMap = new Map(p.stages.map(s => [s.stage, s]))

  function handleAdvance(stage: string) {
    mut.advanceStage.mutate({ id: p.id, stage })
  }

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
                <span className="text-sm text-secondary">
                  Stage: <strong>{STAGE_LABELS[p.current_stage] ?? p.current_stage}</strong>
                </span>
              )}
              {p.topic_id && (
                <span className="text-xs text-muted">Topic {p.topic_id}</span>
              )}
            </div>
          </div>
          <div className="flex gap-2 flex-wrap">
            {p.status === 'running' && (
              <button className="btn btn-secondary btn-sm" onClick={() => mut.pause.mutate(p.id)} disabled={mut.pause.isPending}>Pause</button>
            )}
            {p.status === 'paused' && (
              <button className="btn btn-primary btn-sm" onClick={() => mut.resume.mutate(p.id)} disabled={mut.resume.isPending}>Resume</button>
            )}
            {p.status === 'failed' && (
              <button className="btn btn-secondary btn-sm" onClick={() => mut.recover.mutate(p.id)} disabled={mut.recover.isPending}>Recover</button>
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

        {p.error_message && <div className="error-state mt-4">⚠ {p.error_message}</div>}
        {p.blocked_reason && (
          <div className="diagnostic-finding diagnostic-finding-warning mt-4">⏸ {p.blocked_reason}</div>
        )}
        {mut.advanceStage.error && (
          <div role="alert" className="error-state mt-4">
            {(mut.advanceStage.error as Error).message}
          </div>
        )}
      </div>

      {/* Stage history with drilldown */}
      <section className="section">
        <div className="section-header">
          <h3 className="section-title">Stage History</h3>
          <p className="text-xs text-muted">Click a stage row to inspect artifacts and diagnostics.</p>
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
              {STAGES.map(stageName => (
                <StageRow
                  key={stageName}
                  workspaceId={workspaceId}
                  pipelineId={p.id}
                  stageName={stageName}
                  stage={stageMap.get(stageName)}
                  isCurrentStage={p.current_stage === stageName}
                  onAdvance={handleAdvance}
                  advancePending={mut.advanceStage.isPending}
                />
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Execute current stage */}
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
              <div className="error-state mt-4">{(mut.executeStage.error as Error).message}</div>
            )}
          </div>
        </section>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function Pipelines() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [showStart, setShowStart] = useState(false)

  const { data: pipelines, isLoading, error, refetch } = usePipelines(wid, statusFilter || undefined)

  if (!wid) return <div className="page-body"><EmptyState icon="▶" title="No workspace selected" /></div>

  const selected = pipelines?.find(p => p.id === selectedId)

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Pipelines</h1>
          <p className="page-subtitle">Content production pipeline studio</p>
        </div>
        <div className="flex gap-3 items-center">
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
          <button className="btn btn-primary" onClick={() => setShowStart(true)}>
            Start Pipeline
          </button>
        </div>
      </div>

      <StartPipelineModal workspaceId={wid} open={showStart} onClose={() => setShowStart(false)} />

      <div className="page-body">
        {isLoading ? <LoadingState message="Loading pipelines…" /> :
         error ? <ErrorState error={error} retry={refetch} /> :
         !pipelines?.length ? (
           <EmptyState
             icon="▶"
             title="No pipelines"
             description={statusFilter ? `No pipelines with status "${statusFilter}"` : 'Start your first pipeline to kick off content production.'}
             action={!statusFilter ? <button className="btn btn-primary" onClick={() => setShowStart(true)}>Start Pipeline</button> : undefined}
           />
         ) : (
           <div style={{ display: 'grid', gridTemplateColumns: selected ? '340px 1fr' : '1fr', gap: 'var(--sp-6)' }}>
             <div>
               {pipelines.map(p => (
                 <button
                   key={p.id}
                   onClick={() => setSelectedId(p.id === selectedId ? null : p.id)}
                   style={{
                     display: 'block', width: '100%', textAlign: 'left',
                     padding: 'var(--sp-4)',
                     background: p.id === selectedId ? 'var(--accent-subtle)' : 'var(--surface-card)',
                     border: `1px solid ${p.id === selectedId ? 'var(--accent)' : 'var(--border)'}`,
                     borderRadius: 'var(--radius-lg)', marginBottom: 'var(--sp-3)',
                     cursor: 'pointer', transition: 'all var(--transition-fast)',
                   }}
                   aria-pressed={p.id === selectedId}
                   aria-label={`Pipeline ${p.id.slice(0, 8)}`}
                 >
                   <div className="flex items-center justify-between gap-2">
                     <span className="font-mono text-xs text-muted">{p.id.slice(0, 12)}…</span>
                     <StatusBadge status={p.status} />
                   </div>
                   {p.current_stage && <p className="text-sm font-600 mt-2">{STAGE_LABELS[p.current_stage] ?? p.current_stage}</p>}
                   {p.topic_id && <p className="text-xs text-muted">Topic {p.topic_id}</p>}
                   <PipelineStageBar stages={p.stages} />
                   <p className="text-xs text-muted mt-2">{p.created_at.slice(0, 10)}</p>
                 </button>
               ))}
             </div>

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
