"""Phase 7: Visual Intelligence Engine.

Root package for the Visual Intelligence subsystem. Current modules:
  - planner         — deterministic scene manifest orchestration
  - asset_strategy  — deterministic asset recommendation per scene
  - repository      — scene manifest persistence and review workflow
  - hashing         — immutable input hash for idempotent manifests
  - models          — domain models (drafts + frozen Pydantic projections)
  - constants       — shot types, camera grammar, transitions, asset categories
  - errors          — domain exceptions
  - cli             — Typer sub-app for operator workflows

Future modules will extend this package without architectural rewrites:
  providers/   — stock footage, AI image, licensing verification adapters
  analytics    — learning from review events (training signal consumer)
  optimizer    — retention-driven manifest improvement
"""
