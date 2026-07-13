# Tasks

## Completed
- **Phase 0: Planning & environment** — repository structure, documentation
  files, virtual environment, diagnostic CLI (`version`, `doctor`), pytest
  and Ruff configured. All tests pass.
- **Phase 1: Core data model** — SQLite persistence with versioned schema;
  `Topic`, `Source`, `Script`, `Run` Pydantic models; full repository layer;
  Typer CLI with `topics`, `sources`, `scripts`, `runs` subcommands;
  structured logging; configuration module. 63 tests pass.

## Current
- **Phase 2: Structured script generation** — LLM provider interface, Claude
  integration, schema validation, dry-run mode.

## Next
- **Phase 3: Script criticism & human approval**

## Future
- Phase 2: Structured script generation (LLM provider interface, Claude,
  schema validation, dry-run mode)
- Phase 3: Script criticism & human approval
- Phase 4: Scene manifests & asset tracking
- Phase 5: Narration
- Phase 6: Video rendering (FFmpeg)
- Phase 7: Local end-to-end pipeline
- Phase 8: YouTube integration
- Phase 9: Metrics & analytics
- Phase 10: n8n orchestration
- Phase 11: Additional platforms
- Phase 12: Scaling

## Deferred
- CI (GitHub Actions) — reasonable once the repo is on GitHub and Phase 0
  is verified locally; not required for Phase 0's definition of done.
- Structured logging — introduced starting Phase 1, once there are
  operations (DB writes, API calls) worth logging.
