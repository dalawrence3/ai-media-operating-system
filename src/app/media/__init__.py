"""Phase 8 — Rendering Engine.

Consumes approved upstream artifacts:
  ApprovedSceneManifest + ApprovedNarrationRun + ApprovedCaptionRun + Resolved Assets
        ↓
  Canonical RenderManifest
        ↓
  RenderBackend (FFmpeg)
        ↓
  Derived MP4
        ↓
  Human Render Review

Engine principles:
- One canonical input boundary: RenderManifest
- One canonical output: derived MP4 (RenderJob)
- Provider-neutral: RenderBackend is a Protocol
- Deterministic: same RenderManifest → same input_hash
- Immutable provenance: all decisions stored in DB
- Append-only human feedback via render_review_events
- Historical renders are superseded, never overwritten
- Rendering quality is separable from rendering-provider implementation
"""
