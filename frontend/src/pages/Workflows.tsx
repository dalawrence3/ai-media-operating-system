/* Workflows — schedules and event history */

import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { Modal } from '@/components/common/Modal'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'

const SCHEDULE_TYPES = ['cron', 'interval', 'once', 'after_event'] as const
const OPERATION_TYPES = [
  'start_pipeline',
  'pause_pipeline',
  'resume_pipeline',
  'cancel_pipeline',
] as const

function CreateScheduleModal({ workspaceId, open, onClose }: {
  workspaceId: string
  open: boolean
  onClose: () => void
}) {
  const [name, setName] = useState('')
  const [operationType, setOperationType] = useState<string>(OPERATION_TYPES[0])
  const [scheduleType, setScheduleType] = useState<string>(SCHEDULE_TYPES[0])
  const [configRaw, setConfigRaw] = useState('{}')
  const [timezone, setTimezone] = useState('UTC')
  const [configError, setConfigError] = useState<string | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)

  const qc = useQueryClient()
  const createSchedule = useMutation({
    mutationFn: (body: Parameters<typeof api.createSchedule>[1]) =>
      api.createSchedule(workspaceId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['schedules', workspaceId] })
    },
  })

  function validateConfig() {
    try {
      JSON.parse(configRaw)
      setConfigError(null)
      return true
    } catch {
      setConfigError('Must be valid JSON')
      return false
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setApiError(null)
    if (!validateConfig()) return
    try {
      await createSchedule.mutateAsync({
        name: name.trim(),
        operation_type: operationType,
        schedule_type: scheduleType,
        schedule_config: JSON.parse(configRaw) as Record<string, unknown>,
        timezone: timezone.trim() || 'UTC',
      })
      setName(''); setConfigRaw('{}'); setApiError(null)
      onClose()
    } catch (err) {
      setApiError((err as Error).message)
    }
  }

  function handleClose() {
    setName(''); setOperationType(OPERATION_TYPES[0]); setScheduleType(SCHEDULE_TYPES[0])
    setConfigRaw('{}'); setTimezone('UTC'); setConfigError(null); setApiError(null)
    onClose()
  }

  const isValid = name.trim().length > 0 && !configError

  return (
    <Modal
      open={open}
      title="New Schedule"
      onClose={handleClose}
      footer={
        <>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleClose}
            disabled={createSchedule.isPending}
          >Cancel</button>
          <button
            type="submit"
            form="create-schedule-form"
            className="btn btn-primary"
            disabled={!isValid || createSchedule.isPending}
          >{createSchedule.isPending ? 'Creating…' : 'Create Schedule'}</button>
        </>
      }
    >
      <form id="create-schedule-form" onSubmit={handleSubmit}>
        <div className="form-group">
          <label htmlFor="sched-name" className="form-label">Schedule name <span aria-hidden="true">*</span></label>
          <input
            id="sched-name"
            className="form-input"
            type="text"
            value={name}
            onChange={e => setName(e.target.value)}
            required
            aria-required="true"
            autoFocus
            placeholder="Daily publish"
          />
        </div>
        <div className="form-group">
          <label htmlFor="sched-operation" className="form-label">Operation type</label>
          <select
            id="sched-operation"
            className="field-select"
            value={operationType}
            onChange={e => setOperationType(e.target.value)}
          >
            {OPERATION_TYPES.map(t => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
        <div className="form-group">
          <label htmlFor="sched-type" className="form-label">Schedule type</label>
          <select
            id="sched-type"
            className="field-select"
            value={scheduleType}
            onChange={e => setScheduleType(e.target.value)}
          >
            {SCHEDULE_TYPES.map(t => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
        <div className="form-group">
          <label htmlFor="sched-config" className="form-label">
            Schedule config <span className="text-muted">(JSON)</span>
          </label>
          <textarea
            id="sched-config"
            className="form-input"
            value={configRaw}
            onChange={e => { setConfigRaw(e.target.value); setConfigError(null) }}
            onBlur={validateConfig}
            rows={3}
            aria-describedby={configError ? 'sched-config-error' : undefined}
          />
          {configError && (
            <p id="sched-config-error" className="form-error" role="alert">{configError}</p>
          )}
        </div>
        <div className="form-group">
          <label htmlFor="sched-tz" className="form-label">Timezone</label>
          <input
            id="sched-tz"
            className="form-input"
            type="text"
            value={timezone}
            onChange={e => setTimezone(e.target.value)}
            placeholder="UTC"
          />
        </div>
        {apiError && (
          <div role="alert" className="form-error">{apiError}</div>
        )}
      </form>
    </Modal>
  )
}

export function Workflows() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''
  const [showCreate, setShowCreate] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const qc = useQueryClient()

  const { data: schedules, isLoading, error, refetch } = useQuery({
    queryKey: ['schedules', wid],
    queryFn: () => api.listSchedules(wid),
    enabled: !!wid,
  })

  function onMutError(err: unknown) {
    setActionError((err as Error).message ?? 'Action failed — please try again.')
  }

  const pauseMut = useMutation({
    mutationFn: (id: string) => api.pauseSchedule(wid, id),
    onSuccess: () => { setActionError(null); void qc.invalidateQueries({ queryKey: ['schedules', wid] }) },
    onError: onMutError,
  })

  const resumeMut = useMutation({
    mutationFn: (id: string) => api.resumeSchedule(wid, id),
    onSuccess: () => { setActionError(null); void qc.invalidateQueries({ queryKey: ['schedules', wid] }) },
    onError: onMutError,
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.deleteSchedule(wid, id),
    onSuccess: () => { setActionError(null); void qc.invalidateQueries({ queryKey: ['schedules', wid] }) },
    onError: onMutError,
  })

  const { data: events } = useQuery({
    queryKey: ['events', wid],
    queryFn: () => api.listEvents(wid, undefined, 20),
    enabled: !!wid,
  })

  if (!wid) return (
    <div className="page-body">
      <EmptyState icon="🔄" title="No workspace selected" />
    </div>
  )

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Workflows</h1>
          <p className="page-subtitle">Schedules and event history</p>
        </div>
        <button
          className="btn btn-primary"
          onClick={() => setShowCreate(true)}
          aria-label="Create new schedule"
        >+ New Schedule</button>
      </div>

      <CreateScheduleModal
        workspaceId={wid}
        open={showCreate}
        onClose={() => setShowCreate(false)}
      />

      <div className="page-body">
        {actionError && (
          <div role="alert" className="form-error" style={{ marginBottom: '1rem' }}>
            {actionError}
            <button
              type="button"
              className="btn btn-sm"
              style={{ marginLeft: '0.75rem' }}
              onClick={() => setActionError(null)}
              aria-label="Dismiss error"
            >✕</button>
          </div>
        )}
        <section className="section">
          <div className="section-header">
            <h2 className="section-title">Schedules</h2>
          </div>
          {isLoading ? <LoadingState /> :
           error ? <ErrorState error={error} retry={refetch} /> :
           !schedules?.length ? (
             <EmptyState
               icon="🗓"
               title="No schedules defined"
               description="Create a schedule to automate recurring pipeline operations."
               action={
                 <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
                   + New Schedule
                 </button>
               }
             />
           ) : (
             <div className="table-wrapper">
               <table className="data-table">
                 <thead>
                   <tr>
                     <th>Name</th>
                     <th>Type</th>
                     <th>Operation</th>
                     <th>Active</th>
                     <th>Last Run</th>
                     <th>Next Run</th>
                     <th>Actions</th>
                   </tr>
                 </thead>
                 <tbody>
                   {schedules.map(s => (
                     <tr key={s.id}>
                       <td className="font-600">{s.name}</td>
                       <td><span className="tag">{s.schedule_type}</span></td>
                       <td className="text-sm">{s.operation_type}</td>
                       <td>{s.is_active ? <span className="badge badge-healthy">Active</span> : <span className="badge badge-neutral">Inactive</span>}</td>
                       <td className="text-xs text-muted">{s.last_run_at?.slice(0,16).replace('T',' ') ?? '—'}</td>
                       <td className="text-xs text-muted">{s.next_run_at?.slice(0,16).replace('T',' ') ?? '—'}</td>
                       <td>
                         <div className="flex gap-2">
                           {s.is_active ? (
                             <button
                               className="btn btn-secondary btn-sm"
                               onClick={() => pauseMut.mutate(s.id)}
                               disabled={pauseMut.isPending}
                               aria-label={`Pause schedule ${s.name}`}
                             >Pause</button>
                           ) : (
                             <button
                               className="btn btn-secondary btn-sm"
                               onClick={() => resumeMut.mutate(s.id)}
                               disabled={resumeMut.isPending}
                               aria-label={`Resume schedule ${s.name}`}
                             >Resume</button>
                           )}
                           <button
                             className="btn btn-danger btn-sm"
                             onClick={() => {
                               if (confirm(`Delete schedule "${s.name}"?`)) {
                                 deleteMut.mutate(s.id)
                               }
                             }}
                             disabled={deleteMut.isPending}
                             aria-label={`Delete schedule ${s.name}`}
                           >Delete</button>
                         </div>
                       </td>
                     </tr>
                   ))}
                 </tbody>
               </table>
             </div>
           )}
        </section>

        {events && events.length > 0 && (
          <section className="section">
            <div className="section-header">
              <h2 className="section-title">Recent Events</h2>
            </div>
            <div className="table-wrapper">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Actor</th>
                    <th>Correlation</th>
                    <th>When</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((ev, i) => (
                    <tr key={i}>
                      <td><span className="tag">{ev.event_type}</span></td>
                      <td className="text-sm text-muted">{ev.actor}</td>
                      <td className="font-mono text-xs text-muted">{ev.correlation_id?.slice(0,12) ?? '—'}</td>
                      <td className="text-xs text-muted">{ev.created_at.slice(0,16).replace('T',' ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </div>
    </>
  )
}
