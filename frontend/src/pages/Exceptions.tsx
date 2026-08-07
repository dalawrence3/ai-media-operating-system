/* M14.5 — Exception Center */

import { useParams } from 'react-router-dom'
import { StatusBadge } from '@/components/common/StatusBadge'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { useExceptionQueue } from '@/hooks/useWorkspace'

export function Exceptions() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''
  const { data: items, isLoading, error, refetch } = useExceptionQueue(wid)

  if (!wid) return (
    <div className="page-body">
      <EmptyState icon="⚠" title="No workspace selected" />
    </div>
  )

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Exception Center</h1>
          <p className="page-subtitle">Centralized operational exceptions</p>
        </div>
      </div>

      <div className="page-body">
        {isLoading ? <LoadingState message="Loading exceptions…" /> :
         error ? <ErrorState error={error} retry={refetch} /> :
         !items?.length ? (
           <EmptyState
             icon="✅"
             title="No active exceptions"
             description="No operational exceptions are currently recorded."
           />
         ) : (
           <div>
             {items.map((e, i) => (
               <div key={i} className="card mb-4">
                 <div className="card-header">
                   <div>
                     <div className="flex gap-2 items-center">
                       <span className="tag">{e.exception_type}</span>
                       <StatusBadge status={e.severity} />
                     </div>
                     <p className="card-subtitle mt-1">{e.description}</p>
                   </div>
                   <p className="text-xs text-muted">{e.occurred_at.slice(0,16).replace('T',' ')}</p>
                 </div>

                 <div className="flex gap-6 flex-wrap mt-3">
                   <div>
                     <p className="text-xs text-muted">Entity ID</p>
                     <p className="font-mono text-xs">{e.entity_id}</p>
                   </div>
                   <div>
                     <p className="text-xs text-muted">Workspace</p>
                     <p className="font-mono text-xs">{e.workspace_id.slice(0,12)}</p>
                   </div>
                 </div>

                 {Object.keys(e.metadata).length > 0 && (
                   <details className="mt-4">
                     <summary className="text-xs text-muted" style={{ cursor: 'pointer' }}>
                       Diagnostics
                     </summary>
                     <pre className="font-mono text-xs text-secondary mt-2" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                       {JSON.stringify(e.metadata, null, 2)}
                     </pre>
                   </details>
                 )}
               </div>
             ))}
           </div>
         )}
      </div>
    </>
  )
}
