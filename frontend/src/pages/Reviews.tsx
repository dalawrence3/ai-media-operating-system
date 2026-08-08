/* M14.5 — Review Center */

import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { StatusBadge } from '@/components/common/StatusBadge'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { Modal } from '@/components/common/Modal'
import { useReviewQueue } from '@/hooks/useWorkspace'
import { useReviewMutations } from '@/hooks/useReviews'
import type { ReviewItemView } from '@/api/types'

function ReviewAction({
  item,
  workspaceId,
  onDone,
}: {
  item: ReviewItemView
  workspaceId: string
  onDone: () => void
}) {
  const [showReject, setShowReject] = useState(false)
  const [reason, setReason] = useState('')
  const [notes, setNotes] = useState('')
  const { approve, reject } = useReviewMutations(workspaceId)

  function handleApprove() {
    approve.mutate(
      { itemType: item.item_type, itemId: item.item_id, notes },
      { onSuccess: onDone },
    )
  }

  function handleReject() {
    if (!reason.trim()) return
    reject.mutate(
      { itemType: item.item_type, itemId: item.item_id, reason, notes },
      { onSuccess: onDone },
    )
  }

  return (
    <div>
      <div className="flex gap-3 mt-4">
        <button
          className="btn btn-primary btn-sm"
          onClick={handleApprove}
          disabled={approve.isPending}
          aria-label={`Approve ${item.item_type} ${item.item_id}`}
        >
          {approve.isPending ? 'Approving…' : '✓ Approve'}
        </button>
        <button
          className="btn btn-danger btn-sm"
          onClick={() => setShowReject(true)}
          aria-label={`Reject ${item.item_type} ${item.item_id}`}
        >
          ✗ Reject
        </button>
      </div>

      {approve.error && (
        <div className="error-state mt-3">{(approve.error as Error).message}</div>
      )}

      <Modal
        open={showReject}
        title="Reject Review Item"
        onClose={() => setShowReject(false)}
        footer={
          <>
            <button className="btn btn-secondary btn-sm" onClick={() => setShowReject(false)}>
              Cancel
            </button>
            <button
              className="btn btn-danger btn-sm"
              onClick={handleReject}
              disabled={!reason.trim() || reject.isPending}
            >
              {reject.isPending ? 'Rejecting…' : 'Confirm Reject'}
            </button>
          </>
        }
      >
        <div className="field mb-4">
          <label className="field-label" htmlFor="reject-reason">Reason (required)</label>
          <input
            id="reject-reason"
            className="field-input"
            value={reason}
            onChange={e => setReason(e.target.value)}
            placeholder="Describe the issue…"
            aria-required="true"
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="reject-notes">Notes (optional)</label>
          <textarea
            id="reject-notes"
            className="field-input"
            rows={3}
            value={notes}
            onChange={e => setNotes(e.target.value)}
            placeholder="Additional context for the record…"
          />
        </div>
        {reject.error && (
          <div className="error-state mt-3">{(reject.error as Error).message}</div>
        )}
      </Modal>
    </div>
  )
}

export function Reviews() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''
  const [selected, setSelected] = useState<ReviewItemView | null>(null)
  const [typeFilter, setTypeFilter] = useState('')

  const { data: items, isLoading, error, refetch } = useReviewQueue(wid)

  const filtered = typeFilter
    ? (items ?? []).filter(i => i.item_type === typeFilter)
    : (items ?? [])

  const types = [...new Set((items ?? []).map(i => i.item_type))]

  if (!wid) return (
    <div className="page-body">
      <EmptyState icon="✅" title="No workspace selected" />
    </div>
  )

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Review Center</h1>
          <p className="page-subtitle">Unified review queue — {filtered.length} pending</p>
        </div>
        {types.length > 0 && (
          <select
            className="field-select"
            value={typeFilter}
            onChange={e => setTypeFilter(e.target.value)}
            aria-label="Filter by type"
          >
            <option value="">All types</option>
            {types.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        )}
      </div>

      <div className="page-body">
        {isLoading ? <LoadingState message="Loading review queue…" /> :
         error ? <ErrorState error={error} retry={refetch} /> :
         !filtered.length ? (
           <EmptyState
             icon="✅"
             title="No pending reviews"
             description="All review items have been processed."
           />
         ) : (
           <div style={{ display: 'grid', gridTemplateColumns: selected ? '1fr 400px' : '1fr', gap: 'var(--sp-6)' }}>
             <div className="table-wrapper">
               <table className="data-table">
                 <thead>
                   <tr>
                     <th>Type</th>
                     <th>ID</th>
                     <th>Description</th>
                     <th>Status</th>
                     <th>Channel</th>
                     <th>Created</th>
                   </tr>
                 </thead>
                 <tbody>
                   {filtered.map(item => (
                     <tr
                       key={`${item.item_type}:${item.item_id}`}
                       style={{
                         cursor: 'pointer',
                         background: selected?.item_id === item.item_id ? 'var(--accent-subtle)' : undefined,
                       }}
                       onClick={() => setSelected(selected?.item_id === item.item_id ? null : item)}
                       tabIndex={0}
                       role="button"
                       aria-pressed={selected?.item_id === item.item_id}
                       onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') setSelected(item) }}
                     >
                       <td><span className="tag">{item.item_type}</span></td>
                       <td className="font-mono text-xs text-muted">{item.item_id.slice(0,12)}…</td>
                       <td className="truncate" style={{ maxWidth: 280 }}>{item.description}</td>
                       <td><StatusBadge status={item.status} /></td>
                       <td className="text-sm text-muted">{item.channel_id?.slice(0,8) ?? '—'}</td>
                       <td className="text-xs text-muted">{item.created_at.slice(0,10)}</td>
                     </tr>
                   ))}
                 </tbody>
               </table>
             </div>

             {selected && (
               <div className="card" style={{ height: 'fit-content' }}>
                 <h3 className="card-title mb-4">{selected.description}</h3>
                 <div className="flex gap-3 flex-wrap mb-4">
                   <span className="tag">{selected.item_type}</span>
                   <StatusBadge status={selected.status} />
                 </div>
                 <p className="text-xs text-muted mb-1">Item ID</p>
                 <p className="font-mono text-xs mb-4">{selected.item_id}</p>
                 {selected.channel_id && (
                   <>
                     <p className="text-xs text-muted mb-1">Channel</p>
                     <p className="font-mono text-xs mb-4">{selected.channel_id}</p>
                   </>
                 )}
                 <p className="text-xs text-muted mb-1">Created</p>
                 <p className="text-sm mb-4">{selected.created_at.slice(0,19).replace('T',' ')}</p>

                 {Object.keys(selected.metadata).length > 0 && (
                   <>
                     <p className="text-xs text-muted mb-2">Metadata</p>
                     <pre className="font-mono text-xs text-secondary" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                       {JSON.stringify(selected.metadata, null, 2)}
                     </pre>
                   </>
                 )}

                 <ReviewAction
                   item={selected}
                   workspaceId={wid}
                   onDone={() => setSelected(null)}
                 />
               </div>
             )}
           </div>
         )}
      </div>
    </>
  )
}
