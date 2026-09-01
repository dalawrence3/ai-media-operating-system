/* Phase 17B — Content library.
 *
 * A unified, video-first view of everything this channel has published.
 *
 * Scope note: this page is built entirely from the `publications` list,
 * whose real status vocabulary is uploading/uploaded/scheduled/published/
 * failed/deleted. Earlier lifecycle stages (topic → script → production →
 * render, before a publishing_plan exists) are not reachable through any
 * workspace-scoped endpoint today — a confirmed backend gap (see the
 * Phase 17B report) — so filter tabs reflect only the publishing-stage
 * lifecycle that is actually queryable, rather than inventing "Ideas" /
 * "In Production" tabs that would always read empty.
 */

import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { PageHeader } from '@/components/common/PageHeader'
import { VideoCard, type VideoCardData } from '@/components/common/VideoCard'
import { LoadingState } from '@/components/common/LoadingState'
import { ErrorState } from '@/components/common/ErrorState'
import { EmptyState } from '@/components/common/EmptyState'
import { usePublications } from '@/hooks/usePublications'
import type { PublicationListItem } from '@/api/types'

type LifecycleFilter = 'all' | 'published' | 'publishing' | 'failed' | 'archived'

const FILTERS: { key: LifecycleFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'published', label: 'On YouTube' },
  { key: 'publishing', label: 'Publishing' },
  { key: 'failed', label: 'Failed' },
  { key: 'archived', label: 'Archived' },
]

function matchesFilter(pub: PublicationListItem, filter: LifecycleFilter): boolean {
  switch (filter) {
    case 'all': return true
    case 'published': return pub.status === 'published'
    case 'publishing': return ['uploading', 'uploaded', 'scheduled'].includes(pub.status)
    case 'failed': return pub.status === 'failed'
    case 'archived': return pub.status === 'deleted'
  }
}

function toCardData(pub: PublicationListItem): VideoCardData {
  return {
    id: pub.id,
    title: pub.title,
    providerVideoId: pub.provider_video_id,
    visibility: pub.visibility,
    status: pub.status,
    publishedAt: pub.published_at,
    durationMs: pub.render_duration_ms,
    views: null, // channel library intentionally omits per-video analytics fan-out; see detail page
    avgViewPercentage: null,
    topicTitle: pub.topic_title,
  }
}

export function Content() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const wid = workspaceId ?? ''
  const navigate = useNavigate()
  const [filter, setFilter] = useState<LifecycleFilter>('all')

  const { data: publications, isLoading, error } = usePublications(wid)

  const counts = useMemo(() => {
    const c: Record<LifecycleFilter, number> = {
      all: 0, published: 0, publishing: 0, failed: 0, archived: 0,
    }
    for (const pub of publications ?? []) {
      for (const f of FILTERS) {
        if (matchesFilter(pub, f.key)) c[f.key] += 1
      }
    }
    return c
  }, [publications])

  const filtered = (publications ?? []).filter(p => matchesFilter(p, filter))

  if (!wid) {
    return (
      <div className="page-body">
        <EmptyState icon="🏢" title="No workspace selected" />
      </div>
    )
  }

  if (isLoading) return <LoadingState message="Loading content…" />
  if (error) return <ErrorState error={error} />

  return (
    <>
      <PageHeader
        title="Content"
        subtitle={`${counts.all} video${counts.all === 1 ? '' : 's'}`}
      />

      <div className="page-body">
        <div className="segmented" role="tablist" aria-label="Filter by lifecycle status" style={{ marginBottom: 'var(--sp-5)' }}>
          {FILTERS.map(f => (
            <button
              key={f.key}
              role="tab"
              aria-selected={filter === f.key}
              className={`btn btn-sm ${filter === f.key ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setFilter(f.key)}
            >
              {f.label} ({counts[f.key]})
            </button>
          ))}
        </div>

        {filtered.length === 0 ? (
          <EmptyState
            icon="🎬"
            title={counts.all === 0 ? 'No videos yet' : `No ${filter === 'all' ? '' : filter} videos`}
            description={
              counts.all === 0
                ? 'Videos will appear here once a pipeline completes the publishing stage.'
                : undefined
            }
          />
        ) : (
          <div className="video-grid">
            {filtered.map(pub => (
              <VideoCard
                key={pub.id}
                video={toCardData(pub)}
                onClick={() => navigate(`/workspaces/${wid}/content/${pub.id}`)}
              />
            ))}
          </div>
        )}
      </div>
    </>
  )
}
