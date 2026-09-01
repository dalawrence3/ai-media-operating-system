import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { OptimizationRecommendation } from '@/api/types'

export function useRecommendations(
  workspaceId: string,
  status?: string,
  domain?: string,
  publicationId?: number,
) {
  return useQuery({
    queryKey: ['recommendations', workspaceId, status, domain, publicationId],
    queryFn: () => api.listRecommendations(workspaceId, status, domain, publicationId),
    enabled: !!workspaceId,
  })
}

/** Recommendations for a specific set of publications, merged and sorted.
 *
 * The workspace-wide recommendations endpoint (no publication_id) resolves
 * topic scope via app_pipeline_executions, which has zero rows for this
 * workspace (a confirmed backend gap — see Phase 17B report), so it always
 * returns empty here. Fanning out per-publication_id bypasses that path and
 * is also how DEV fixture recommendations (which carry no publication_id)
 * are naturally excluded — reinforced by an explicit title-prefix filter
 * below in case fixture data is ever linked to a real publication.
 */
export function useChannelRecommendations(workspaceId: string, publicationIds: number[]) {
  const queries = useQueries({
    queries: publicationIds.map(id => ({
      queryKey: ['recommendations', workspaceId, undefined, undefined, id],
      queryFn: () => api.listRecommendations(workspaceId, undefined, undefined, id),
      enabled: !!workspaceId,
    })),
  })

  const recommendations: OptimizationRecommendation[] = queries
    .flatMap(q => q.data ?? [])
    .filter(r => !r.title.startsWith('[DEV]'))
    .sort((a, b) => b.created_at.localeCompare(a.created_at))

  return {
    recommendations,
    isLoading: queries.some(q => q.isLoading),
    error: queries.find(q => q.error)?.error ?? null,
  }
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

// ── Cross-publication learning (Phase 17D) ─────────────────────────────────

export function useCrossPublicationLearning(workspaceId: string, channelId: string | null) {
  return useQuery({
    queryKey: ['cross-publication', workspaceId, channelId],
    queryFn: () => api.getCrossPublicationLearning(workspaceId, channelId!),
    enabled: !!workspaceId && !!channelId,
  })
}

// ── Market intelligence (Phase 17D) ─────────────────────────────────────────

export function useMarketOpportunities(workspaceId: string, cpChannelId: string | null) {
  return useQuery({
    queryKey: ['market-opportunities', workspaceId, cpChannelId],
    queryFn: () => api.listMarketOpportunities(workspaceId, cpChannelId!),
    enabled: !!workspaceId && !!cpChannelId,
  })
}

export function useMarketExperiments(workspaceId: string, cpChannelId: string | null) {
  return useQuery({
    queryKey: ['market-experiments', workspaceId, cpChannelId],
    queryFn: () => api.listMarketExperiments(workspaceId, cpChannelId!),
    enabled: !!workspaceId && !!cpChannelId,
  })
}

export function useStrategyBriefs(workspaceId: string, cpChannelId: string | null) {
  return useQuery({
    queryKey: ['strategy-briefs', workspaceId, cpChannelId],
    queryFn: () => api.listStrategyBriefs(workspaceId, cpChannelId!),
    enabled: !!workspaceId && !!cpChannelId,
  })
}

export function useOpportunityEvidence(
  workspaceId: string,
  opportunityId: number | null,
  cpChannelId: string | null,
) {
  return useQuery({
    queryKey: ['opportunity-evidence', workspaceId, opportunityId, cpChannelId],
    queryFn: () => api.getOpportunityEvidence(workspaceId, opportunityId!, cpChannelId!),
    enabled: !!workspaceId && opportunityId !== null && !!cpChannelId,
  })
}

/** The channel's recurring market-refresh schedule, if one exists — reuses
    the existing generic schedules list rather than a dedicated endpoint. */
export function useMarketRefreshSchedule(workspaceId: string, cpChannelId: string | null) {
  return useQuery({
    queryKey: ['schedules', workspaceId],
    queryFn: () => api.listSchedules(workspaceId),
    enabled: !!workspaceId && !!cpChannelId,
    select: schedules => schedules.find(
      s => s.operation_type === 'market_refresh' && s.channel_id === cpChannelId,
    ) ?? null,
  })
}
