/* M14.8 — Audit Timeline */

import { useParams } from 'react-router-dom'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { useAuditTimeline } from '@/hooks/useWorkspace'

export function Audit() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''
  const { data: events, isLoading, error, refetch } = useAuditTimeline(wid, 100)

  if (!wid) return (
    <div className="page-body">
      <EmptyState icon="📋" title="No workspace selected" />
    </div>
  )

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Audit Timeline</h1>
          <p className="page-subtitle">Unified chronological activity log</p>
        </div>
      </div>

      <div className="page-body">
        {isLoading ? <LoadingState message="Loading audit events…" /> :
         error ? <ErrorState error={error} retry={refetch} /> :
         !events?.length ? (
           <EmptyState icon="📋" title="No audit events" description="Activity will appear here as the system operates." />
         ) : (
           <div className="card">
             <div className="timeline">
               {events.map((ev, i) => (
                 <div key={i} className="timeline-item">
                   <div className="timeline-dot" aria-hidden="true" />
                   <div className="timeline-content">
                     <p className="timeline-desc">{ev.description}</p>
                     <div className="flex gap-4 flex-wrap mt-1">
                       <span className="timeline-actor">Actor: {ev.actor}</span>
                       <span className="tag" style={{ fontSize: 'var(--font-size-xs)' }}>{ev.event_type}</span>
                       {ev.correlation_id && (
                         <span className="font-mono text-xs text-muted">corr: {ev.correlation_id.slice(0,12)}</span>
                       )}
                     </div>
                     <p className="timeline-ts">{ev.timestamp.slice(0,19).replace('T',' ')}</p>
                   </div>
                 </div>
               ))}
             </div>
           </div>
         )}
      </div>
    </>
  )
}
