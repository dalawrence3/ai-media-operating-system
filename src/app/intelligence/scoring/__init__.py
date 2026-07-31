"""M3.3 versioned scoring and confidence engine."""

from app.intelligence.scoring.engine import SCORER_VERSION, score_opportunity

__all__ = ["score_opportunity", "SCORER_VERSION"]
