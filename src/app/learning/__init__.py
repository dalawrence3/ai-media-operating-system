"""Phase 11 — Learning & Optimization Engine.

Converts accumulated operational history into deterministic, explainable
optimization recommendations.

Strict constraints:
- NO ML training, NO embeddings, NO vector databases.
- NO automatic optimization.  Every recommendation requires human review.
- NO network calls, NO external services.
- Everything deterministic.  Everything reproducible.

Public interface:
- orchestrator.analyze_publication()  — generate recommendations for a publication
- orchestrator.accept_recommendation() — record human acceptance
- orchestrator.reject_recommendation() — record human rejection
- models.OptimizationRecommendation   — immutable recommendation row
- models.LearningRun                  — immutable learning run row
- cli.learn_app                       — ace learn CLI
"""
