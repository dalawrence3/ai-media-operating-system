import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

export function useAnalyticsAggregates(
  workspaceId: string,
  metricName?: string,
  periodType?: string,
) {
  return useQuery({
    queryKey: ['analytics', 'aggregates', workspaceId, metricName, periodType],
    queryFn: () => api.listAnalyticsAggregates(workspaceId, metricName, periodType),
    enabled: !!workspaceId,
  })
}

export function useAnalyticsSnapshots(workspaceId: string, limit?: number) {
  return useQuery({
    queryKey: ['analytics', 'snapshots', workspaceId, limit],
    queryFn: () => api.listAnalyticsSnapshots(workspaceId, limit),
    enabled: !!workspaceId,
  })
}
