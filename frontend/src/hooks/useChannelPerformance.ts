import { useQueries } from '@tanstack/react-query'
import { api } from '@/api/client'
import { usePublications } from '@/hooks/usePublications'
import { computeChannelKpis, type ChannelKpis } from '@/lib/channelMetrics'
import type { PublicationAnalytics, PublicationListItem } from '@/api/types'

export interface PublicationWithAnalytics {
  publication: PublicationListItem
  analytics: PublicationAnalytics | null
  analyticsLoading: boolean
}

interface ChannelPerformance {
  items: PublicationWithAnalytics[]
  kpis: ChannelKpis
  isLoading: boolean
  error: unknown
}

/** Publications for the workspace, each paired with its latest analytics
    snapshot. There is no channel-level analytics endpoint, so this fans out
    one analytics request per publication — acceptable at the channel's
    current scale (a handful of videos), and it reuses the same clean,
    per-publication data path as the publication detail page rather than the
    contaminated analytics_aggregates table (see channelMetrics.ts). */
export function useChannelPerformance(workspaceId: string): ChannelPerformance {
  const publications = usePublications(workspaceId)
  const pubs = publications.data ?? []

  const analyticsQueries = useQueries({
    queries: pubs.map(pub => ({
      queryKey: ['publication-analytics', workspaceId, pub.id],
      queryFn: () => api.getPublicationAnalytics(workspaceId, pub.id),
      enabled: !!workspaceId,
    })),
  })

  const items: PublicationWithAnalytics[] = pubs.map((pub, i) => {
    const q = analyticsQueries[i]
    const analytics = q?.data ?? null
    return {
      publication: pub,
      analytics: analytics && analytics.snapshot_id !== null ? analytics : null,
      analyticsLoading: q?.isLoading ?? false,
    }
  })

  const kpis = computeChannelKpis(
    items.map(i => ({ publicationId: i.publication.id, metrics: i.analytics?.metrics ?? null })),
  )

  return {
    items,
    kpis,
    isLoading: publications.isLoading || analyticsQueries.some(q => q.isLoading),
    error: publications.error ?? analyticsQueries.find(q => q.error)?.error ?? null,
  }
}
