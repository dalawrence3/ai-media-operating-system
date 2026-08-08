/* Workflows — read-only view of workspace events/workflows */

import { useParams } from 'react-router-dom'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

export function Workflows() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''

  const { data: schedules, isLoading, error, refetch } = useQuery({
    queryKey: ['schedules', wid],
    queryFn: () => api.listSchedules(wid),
    enabled: !!wid,
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
      </div>

      <div className="page-body">
        <section className="section">
          <div className="section-header">
            <h2 className="section-title">Schedules</h2>
          </div>
          {isLoading ? <LoadingState /> :
           error ? <ErrorState error={error} retry={refetch} /> :
           !schedules?.length ? (
             <EmptyState icon="🗓" title="No schedules defined" description="Create schedules through the API or CLI." />
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
