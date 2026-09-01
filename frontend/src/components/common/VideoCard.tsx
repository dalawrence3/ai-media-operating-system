import { VideoThumbnail } from '@/components/common/VideoThumbnail'
import { LocalTime } from '@/components/common/LocalTime'
import { StatusBadge } from '@/components/common/StatusBadge'
import { formatCompact, formatDurationMs, formatPercent, formatWatchTime } from '@/lib/format'

export interface VideoCardData {
  id: number
  title: string
  providerVideoId: string | null
  visibility: string
  status: string
  publishedAt: string | null
  durationMs: number | null
  views: number | null
  avgViewPercentage: number | null
  topicTitle?: string | null
  /** Optional — shown as a third stat chip when the caller has it (e.g. the
      Analytics video grid), omitted elsewhere rather than fetched unused. */
  watchTimeSeconds?: number | null
}

interface Props {
  video: VideoCardData
  onClick: () => void
}

/** A video as media content — the shared card used by the Dashboard's Recent
    Videos rail and the Content library grid, so a video looks the same
    wherever it appears. */
export function VideoCard({ video, onClick }: Props) {
  const isPrivate = video.visibility === 'private'

  return (
    <button className="video-card" onClick={onClick} type="button">
      <VideoThumbnail videoId={video.providerVideoId} title={video.title} isPrivate={isPrivate} />
      <div className="video-card-body">
        <p className="video-card-title" title={video.title}>{video.title}</p>
        <div className="video-card-meta">
          <LocalTime value={video.publishedAt} variant="date" fallback="Not published" />
          {video.durationMs !== null && (
            <span className="video-card-duration">{formatDurationMs(video.durationMs)}</span>
          )}
        </div>
        {video.topicTitle && <p className="video-card-topic">{video.topicTitle}</p>}
        <div className="video-card-footer">
          <StatusBadge status={video.visibility} />
          {video.status !== 'published' && <StatusBadge status={video.status} />}
          {video.views !== null && (
            <span className="video-card-stat">{formatCompact(video.views)} views</span>
          )}
          {video.avgViewPercentage !== null && (
            <span className="video-card-stat">
              {formatPercent(video.avgViewPercentage)} viewed
            </span>
          )}
          {video.watchTimeSeconds !== null && video.watchTimeSeconds !== undefined && (
            <span className="video-card-stat">{formatWatchTime(video.watchTimeSeconds)} watched</span>
          )}
        </div>
      </div>
    </button>
  )
}
