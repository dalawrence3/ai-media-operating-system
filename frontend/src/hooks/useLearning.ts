import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'

export function useRecommendations(workspaceId: string, status?: string, domain?: string) {
  return useQuery({
    queryKey: ['recommendations', workspaceId, status, domain],
    queryFn: () => api.listRecommendations(workspaceId, status, domain),
    enabled: !!workspaceId,
  })
}

export function useAcceptRecommendation(workspaceId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, notes }: { id: number; notes?: string }) =>
      api.acceptRecommendation(workspaceId, id, { notes }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['recommendations', workspaceId] }),
  })
}

export function useRejectRecommendation(workspaceId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, notes }: { id: number; notes: string }) =>
      api.rejectRecommendation(workspaceId, id, { notes }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['recommendations', workspaceId] }),
  })
}
