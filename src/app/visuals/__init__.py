"""Semantic visual intelligence.

Plans what each moment of narration should *show*, retrieves or generates it
from a cost-ordered provider stack, scores it for relevance, and gates the
result on production quality before it can become a release candidate.

Nothing in this package is channel-, niche-, or topic-specific.
"""

from app.visuals.constants import VISUAL_ENGINE_VERSION

__all__ = ["VISUAL_ENGINE_VERSION"]
