import { describe, it, expect } from 'vitest'
import { computeChannelKpis, type PublicationMetrics } from '../channelMetrics'

describe('computeChannelKpis', () => {
  it('returns nulls and zero counts for an empty channel', () => {
    const kpis = computeChannelKpis([])
    expect(kpis.totalViews).toBeNull()
    expect(kpis.totalWatchTimeSeconds).toBeNull()
    expect(kpis.totalSubscribersGained).toBeNull()
    expect(kpis.totalSubscribersLost).toBeNull()
    expect(kpis.totalLikes).toBeNull()
    expect(kpis.totalComments).toBeNull()
    expect(kpis.totalShares).toBeNull()
    expect(kpis.totalEngagedViews).toBeNull()
    expect(kpis.avgViewPercentage).toBeNull()
    expect(kpis.videoCount).toBe(0)
    expect(kpis.videosWithDataCount).toBe(0)
  })

  it('sums a single metric across videos with data', () => {
    const items: PublicationMetrics[] = [
      { publicationId: 1, metrics: { views: 474, watch_time_seconds: 10380, subscribers_gained: 0 } },
      { publicationId: 3, metrics: { views: 19, watch_time_seconds: 300, subscribers_gained: 1 } },
    ]
    const kpis = computeChannelKpis(items)
    expect(kpis.totalViews).toBe(493)
    expect(kpis.totalWatchTimeSeconds).toBe(10680)
    expect(kpis.totalSubscribersGained).toBe(1)
    expect(kpis.videoCount).toBe(2)
    expect(kpis.videosWithDataCount).toBe(2)
  })

  it('sums likes, comments, shares, and engaged views across videos with data', () => {
    const items: PublicationMetrics[] = [
      { publicationId: 1, metrics: { likes: 12, comments: 2, shares: 0, engaged_views: 178, subscribers_lost: 0 } },
      { publicationId: 3, metrics: { likes: 1, comments: 0, shares: 0, engaged_views: 13, subscribers_lost: 0 } },
    ]
    const kpis = computeChannelKpis(items)
    expect(kpis.totalLikes).toBe(13)
    expect(kpis.totalComments).toBe(2)
    expect(kpis.totalShares).toBe(0)
    expect(kpis.totalEngagedViews).toBe(191)
    expect(kpis.totalSubscribersLost).toBe(0)
  })

  it('excludes a publication with no analytics from sums rather than treating it as zero', () => {
    const items: PublicationMetrics[] = [
      { publicationId: 1, metrics: { views: 474 } },
      { publicationId: 2, metrics: null }, // no snapshot yet
    ]
    const kpis = computeChannelKpis(items)
    expect(kpis.totalViews).toBe(474)
    expect(kpis.videoCount).toBe(2)
    expect(kpis.videosWithDataCount).toBe(1)
  })

  it('preserves a genuine zero rather than dropping it', () => {
    const items: PublicationMetrics[] = [
      { publicationId: 1, metrics: { subscribers_gained: 0 } },
    ]
    const kpis = computeChannelKpis(items)
    expect(kpis.totalSubscribersGained).toBe(0)
  })

  it('view-weights average_view_percentage rather than taking a flat mean', () => {
    // Live-data-shaped case: pub 1 has 474 views at 95.57%, pub 3 has 19 views at 36.28%.
    // A flat mean would read ~65.9%; the view-weighted figure should sit close to pub 1's value.
    const items: PublicationMetrics[] = [
      { publicationId: 1, metrics: { views: 474, average_view_percentage: 95.57 } },
      { publicationId: 3, metrics: { views: 19, average_view_percentage: 36.28 } },
    ]
    const kpis = computeChannelKpis(items)
    const flatMean = (95.57 + 36.28) / 2
    expect(kpis.avgViewPercentage).not.toBeNull()
    expect(kpis.avgViewPercentage!).toBeGreaterThan(flatMean)
    expect(kpis.avgViewPercentage!).toBeCloseTo(
      (474 * 95.57 + 19 * 36.28) / (474 + 19),
      5,
    )
  })

  it('excludes a video with zero views from the weighted average (cannot carry weight)', () => {
    const items: PublicationMetrics[] = [
      { publicationId: 1, metrics: { views: 100, average_view_percentage: 50 } },
      { publicationId: 2, metrics: { views: 0, average_view_percentage: 100 } },
    ]
    const kpis = computeChannelKpis(items)
    expect(kpis.avgViewPercentage).toBe(50)
  })

  it('ignores NaN and non-numeric metric values instead of corrupting the sum', () => {
    const items: PublicationMetrics[] = [
      { publicationId: 1, metrics: { views: 100 } },
      { publicationId: 2, metrics: { views: NaN } },
    ]
    const kpis = computeChannelKpis(items)
    expect(kpis.totalViews).toBe(100)
  })
})
