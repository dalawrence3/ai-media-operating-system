/* M14.5 — Operations Center */

import { useParams } from 'react-router-dom'
import { useState } from 'react'
import { StatusBadge } from '@/components/common/StatusBadge'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'

export function Operations() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''
  const [statusFilter, setStatusFilter] = useState('')
  const qc = useQueryClient()

  const { data: ops, isLoading, error, refetch } = useQuery({
    queryKey: ['operations', wid, statusFilter],
    queryFn: () => api.listOperations(wid, { status: statusFilter || undefined }),
    enabled: !!wid,
    refetchInterval: 20_000,
  })

  const retryMut = useMutation({
    mutationFn: (id: string) => api.retryOperation(wid, id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['operations', wid] }),
  })

  const cancelMut = useMutation({
    mutationFn: (id: string) => api.cancelOperation(wid, id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['operations', wid] }),
  })

  if (!wid) return (
    <div className="page-body">
      <EmptyState icon="⚙" title="No workspace selected" />
    </div>
  )

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Operations</h1>
          <p className="page-subtitle">Pending, running, and completed operations</p>
        </div>
        <select
          className="field-select"
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          <option value="pending">Pending</option>
          <option value="running">Running</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>

      <div className="page-body">
        {isLoading ? <LoadingState message="Loading operations…" /> :
         error ? <ErrorState error={error} retry={refetch} /> :
         !ops?.length ? (
           <EmptyState icon="⚙" title="No operations" description={statusFilter ? `No operations with status "${statusFilter}"` : 'No operations found.'} />
         ) : (
           <div className="table-wrapper">
             <table className="data-table">
               <thead>
                 <tr>
                   <th>Type</th>
                   <th>Status</th>
                   <th>Actor</th>
                   <th>Correlation</th>
                   <th>Error</th>
                   <th>Created</th>
                   <th>Actions</th>
                 </tr>
               </thead>
               <tbody>
                 {ops.map(op => (
                   <tr key={op.id}>
                     <td><span className="tag">{op.operation_type}</span></td>
                     <td><StatusBadge status={op.status} /></td>
                     <td className="text-sm text-muted">{op.actor}</td>
                     <td className="font-mono text-xs text-muted">{op.correlation_id?.slice(0,12) ?? '—'}</td>
                     <td className="text-xs text-muted truncate" style={{ maxWidth: 180 }}>
                       {op.error_message ?? '—'}
                     </td>
                     <td className="text-xs text-muted">{op.created_at.slice(0,16).replace('T',' ')}</td>
                     <td>
                       <div className="flex gap-2">
                         {op.status === 'failed' && (
                           <button
                             className="btn btn-secondary btn-sm"
                             onClick={() => retryMut.mutate(op.id)}
                             disabled={retryMut.isPending}
                             aria-label={`Retry operation ${op.id}`}
                           >Retry</button>
                         )}
                         {['pending', 'running'].includes(op.status) && (
                           <button
                             className="btn btn-danger btn-sm"
                             onClick={() => { if (confirm('Cancel?')) cancelMut.mutate(op.id) }}
                             disabled={cancelMut.isPending}
                             aria-label={`Cancel operation ${op.id}`}
                           >Cancel</button>
                         )}
                       </div>
                     </td>
                   </tr>
                 ))}
               </tbody>
             </table>
           </div>
         )}
      </div>
    </>
  )
}
