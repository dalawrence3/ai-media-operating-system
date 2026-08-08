/* M14.7 — Experiments UI */

import { useParams } from 'react-router-dom'
import { StatusBadge } from '@/components/common/StatusBadge'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { useExperiments } from '@/hooks/useWorkspace'

export function Experiments() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''
  const { data: experiments, isLoading, error, refetch } = useExperiments(wid)

  if (!wid) return (
    <div className="page-body">
      <EmptyState icon="🧪" title="No workspace selected" />
    </div>
  )

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Experiments</h1>
          <p className="page-subtitle">Active A/B experiments and variants</p>
        </div>
      </div>

      <div className="page-body">
        {isLoading ? <LoadingState message="Loading experiments…" /> :
         error ? <ErrorState error={error} retry={refetch} /> :
         !experiments?.length ? (
           <EmptyState
             icon="🧪"
             title="No active experiments"
             description="Create experiments through the Control Plane CLI to compare content variations."
           />
         ) : (
           <div>
             {experiments.map(exp => (
               <div key={exp.id} className="card mb-4">
                 <div className="card-header">
                   <div>
                     <h3 className="card-title">{exp.name}</h3>
                     <p className="card-subtitle mt-1">{exp.hypothesis}</p>
                   </div>
                   <StatusBadge status={exp.status} />
                 </div>

                 <div className="flex gap-6 flex-wrap mt-4">
                   <div>
                     <p className="text-xs text-muted">Primary Metric</p>
                     <p className="font-mono text-sm">{exp.primary_metric}</p>
                   </div>
                   <div>
                     <p className="text-xs text-muted">Start</p>
                     <p className="text-sm">{exp.start_at?.slice(0,10) ?? '—'}</p>
                   </div>
                   <div>
                     <p className="text-xs text-muted">End</p>
                     <p className="text-sm">{exp.end_at?.slice(0,10) ?? '—'}</p>
                   </div>
                   <div>
                     <p className="text-xs text-muted">ID</p>
                     <p className="font-mono text-xs text-muted">{exp.id}</p>
                   </div>
                 </div>

                 <div className="diagnostic-finding diagnostic-finding-info mt-4">
                   <span>Experiments are immutable once active. No auto-promotion of winners. Result review requires manual approval through ApplicationService.</span>
                 </div>
               </div>
             ))}
           </div>
         )}
      </div>
    </>
  )
}
