import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'

export function useReviewMutations(workspaceId: string) {
  const qc = useQueryClient()
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['review-queue', workspaceId] })
  }

  const approve = useMutation({
    mutationFn: ({ itemType, itemId, notes }: { itemType: string; itemId: string; notes?: string }) =>
      api.approveReviewItem(workspaceId, itemType, itemId, notes),
    onSuccess: invalidate,
  })

  const reject = useMutation({
    mutationFn: ({
      itemType,
      itemId,
      reason,
      notes,
    }: {
      itemType: string
      itemId: string
      reason: string
      notes?: string
    }) => api.rejectReviewItem(workspaceId, itemType, itemId, reason, notes),
    onSuccess: invalidate,
  })

  return { approve, reject }
}
