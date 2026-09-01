import { useState } from 'react'

interface Props {
  /** YouTube video ID. Null for content that has not been uploaded. */
  videoId: string | null | undefined
  title: string
  /** Private videos get a lock overlay on the thumbnail. */
  isPrivate?: boolean
  size?: 'sm' | 'md'
}

/** Static YouTube thumbnail.

    Loads the poster image only — no iframe, no embed script, no autoplay.
    Falls back to a neutral placeholder when the ID is missing or the image
    fails to load (deleted/unlisted/network blocked). Private videos attempt
    to load the thumbnail (ytimg serves it regardless of privacy state) and
    display a lock overlay to indicate restricted visibility.
*/
export function VideoThumbnail({
  videoId,
  title,
  isPrivate = false,
  size = 'md',
}: Props) {
  const [failed, setFailed] = useState(false)

  const usePlaceholder = !videoId || failed

  if (usePlaceholder) {
    return (
      <div
        className={`video-thumb video-thumb-${size} video-thumb-placeholder`}
        role="img"
        aria-label={
          isPrivate
            ? `${title} — private video, no preview available`
            : `${title} — no preview available`
        }
      >
        <span aria-hidden="true">{isPrivate ? '🔒' : '🎬'}</span>
      </div>
    )
  }

  return (
    <div className={`video-thumb video-thumb-${size}`} style={{ position: 'relative', overflow: 'hidden' }}>
      <img
        src={`https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`}
        alt=""
        loading="lazy"
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
        style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
      />
      {isPrivate && (
        <span className="video-thumb-private-overlay" aria-label="Private video">
          🔒
        </span>
      )}
    </div>
  )
}
