import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

export function useWorkspaces(status?: string) {
  return useQuery({
    queryKey: ['workspaces', status],
    queryFn: () => api.listWorkspaces(status),
  })
}

export function useWorkspaceSummary(workspaceId: string) {
  return useQuery({
    queryKey: ['workspace', workspaceId],
    queryFn: () => api.getWorkspaceSummary(workspaceId),
    enabled: !!workspaceId,
  })
}

export function useControlCenter(workspaceId: string) {
  return useQuery({
    queryKey: ['control-center', workspaceId],
    queryFn: () => api.getControlCenter(workspaceId),
    enabled: !!workspaceId,
    refetchInterval: 30_000,
  })
}

export function useHealth(workspaceId: string) {
  return useQuery({
    queryKey: ['health', workspaceId],
    queryFn: () => api.getHealth(workspaceId),
    enabled: !!workspaceId,
    refetchInterval: 60_000,
  })
}

export function useReviewQueue(workspaceId: string) {
  return useQuery({
    queryKey: ['review-queue', workspaceId],
    queryFn: () => api.getReviewQueue(workspaceId),
    enabled: !!workspaceId,
    refetchInterval: 30_000,
  })
}

export function useExceptionQueue(workspaceId: string) {
  return useQuery({
    queryKey: ['exceptions', workspaceId],
    queryFn: () => api.getExceptionQueue(workspaceId),
    enabled: !!workspaceId,
    refetchInterval: 30_000,
  })
}

export function useCosts(workspaceId: string, period?: string) {
  return useQuery({
    queryKey: ['costs', workspaceId, period],
    queryFn: () => api.getCosts(workspaceId, period),
    enabled: !!workspaceId,
  })
}

export function useAuditTimeline(workspaceId: string, limit?: number) {
  return useQuery({
    queryKey: ['audit', workspaceId, limit],
    queryFn: () => api.getAuditTimeline(workspaceId, limit),
    enabled: !!workspaceId,
  })
}

export function useExperiments(workspaceId: string) {
  return useQuery({
    queryKey: ['experiments', workspaceId],
    queryFn: () => api.listExperiments(workspaceId),
    enabled: !!workspaceId,
  })
}
