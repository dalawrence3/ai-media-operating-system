import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'

export function useTopics(workspaceId: string) {
  return useQuery({
    queryKey: ['topics', workspaceId],
    queryFn: () => api.listTopics(workspaceId),
    enabled: !!workspaceId,
  })
}

export function useCreateTopic(workspaceId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { title: string; angle?: string }) =>
      api.createTopic(workspaceId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['topics', workspaceId] })
    },
  })
}
