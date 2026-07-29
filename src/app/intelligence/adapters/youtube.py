"""YouTubeDataAPIAdapter — REST client for YouTube Data API v3."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.intelligence.adapters.base import EvidenceItem, OpportunityCandidate
from app.intelligence.dedup import normalize_topic
from app.intelligence.models import AdapterName, Channel, ChannelProfileVersion

_YOUTUBE_BASE = "https://www.googleapis.com/youtube/v3"
_SEARCH_QUOTA_COST = 100
_VIDEOS_QUOTA_COST = 1


class YouTubeDataAPIAdapter:
    """
    Calls YouTube Data API v3 search.list and videos.list.

    Pass `client` to inject a stub in tests; otherwise a real httpx.Client is created.
    Call close() when done if you did not inject a client.
    """

    def __init__(self, api_key: str = "", client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(timeout=30.0)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def run(
        self,
        topics: list[str],
        channel: Channel,
        profile: ChannelProfileVersion,
    ) -> tuple[list[OpportunityCandidate], int]:
        quota_used = 0
        candidates: list[OpportunityCandidate] = []
        max_candidates = profile.max_candidates_per_run

        for query in topics:
            if len(candidates) >= max_candidates:
                break
            remaining = max_candidates - len(candidates)
            max_results = min(remaining, 50)

            search_resp = self._client.get(
                f"{_YOUTUBE_BASE}/search",
                params={
                    "q": query,
                    "part": "snippet",
                    "type": "video",
                    "maxResults": max_results,
                    "key": self._api_key,
                },
            )
            search_resp.raise_for_status()
            quota_used += _SEARCH_QUOTA_COST

            video_ids = [item["id"]["videoId"] for item in search_resp.json().get("items", [])]
            if not video_ids:
                continue

            videos_resp = self._client.get(
                f"{_YOUTUBE_BASE}/videos",
                params={
                    "id": ",".join(video_ids),
                    "part": "snippet,statistics,contentDetails",
                    "key": self._api_key,
                },
            )
            videos_resp.raise_for_status()
            quota_used += _VIDEOS_QUOTA_COST

            for item in videos_resp.json().get("items", [])[:remaining]:
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})

                raw_topic = snippet.get("title", query)
                normalized = normalize_topic(raw_topic)
                if not normalized:
                    continue

                signal_age_days: float | None = None
                published_at = snippet.get("publishedAt", "")
                if published_at:
                    try:
                        pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                        signal_age_days = float((datetime.now(UTC) - pub_dt).days)
                    except ValueError:
                        pass

                evidence: list[EvidenceItem] = []
                if "viewCount" in stats:
                    evidence.append(
                        EvidenceItem(
                            evidence_type="view_count",
                            evidence_value=float(stats["viewCount"]),
                            evidence_unit="views",
                            source_label="youtube_data_api:videos.list",
                        )
                    )
                if "likeCount" in stats:
                    evidence.append(
                        EvidenceItem(
                            evidence_type="like_count",
                            evidence_value=float(stats["likeCount"]),
                            evidence_unit="count",
                            source_label="youtube_data_api:videos.list",
                        )
                    )
                if "commentCount" in stats:
                    evidence.append(
                        EvidenceItem(
                            evidence_type="comment_count",
                            evidence_value=float(stats["commentCount"]),
                            evidence_unit="count",
                            source_label="youtube_data_api:videos.list",
                        )
                    )
                if signal_age_days is not None:
                    evidence.append(
                        EvidenceItem(
                            evidence_type="top_video_age_days",
                            evidence_value=signal_age_days,
                            evidence_unit="days",
                            source_label="youtube_data_api:videos.list",
                        )
                    )

                candidates.append(
                    OpportunityCandidate(
                        raw_topic=raw_topic,
                        normalized_topic=normalized,
                        adapter_name=AdapterName.youtube_data_api,
                        raw_payload=item,
                        evidence=evidence,
                        signal_age_days=signal_age_days,
                        source_quality_tier="high",
                    )
                )

        return candidates, quota_used
