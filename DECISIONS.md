# Decisions

## Python 3.13 over 3.14

**Decision:** Target Python 3.13.

**Alternatives considered:** Python 3.14 (the newest stable release as of
mid-2026).

**Reasoning:** This project will eventually depend on several third-party
SDKs (Anthropic's API client, `google-api-python-client` for YouTube, a TTS
provider's SDK, and possibly data/analysis libraries in Phase 9). Third-party
packages — especially ones with compiled extensions — typically take a
release cycle or two to fully support a new Python version. 3.13 has had
roughly a year of real-world usage and broad wheel availability, while 3.14
is newer and carries more first-mover risk for a project whose priority is
reliability over newest language features. Revisit if a specific dependency
later requires 3.14+.

## `src/` layout over flat package layout

**Decision:** Package code lives under `src/app/`, installed in editable mode.

**Alternatives considered:** A flat layout with `app/` at the repository root.

**Reasoning:** See ARCHITECTURE.md — the `src/` layout catches packaging bugs
(imports that only work because of the working directory, not because the
package is actually installed) immediately instead of at first clean-machine
install. One-time setup cost, no ongoing complexity.

## Typer for Phase 1+ CLI (replacing Phase 0 argparse)

**Decision:** Switched from `argparse` to Typer starting in Phase 1.

**Reasoning:** Phase 1 adds four subcommand groups (`topics`, `sources`,
`scripts`, `runs`) each with multiple commands and typed arguments. Hand-written
`argparse` subparsers at that scale are verbose and error-prone. Typer
generates help text, validates types, and handles `Optional` arguments with
zero boilerplate — the complexity now justifies the dependency. The Phase 0
decision to defer this was correct; we revisited it at the right time.

## argparse (stdlib) over Typer/Click for Phase 0

**Decision:** Use the standard library's `argparse` for the Phase 0
diagnostic CLI, not a third-party CLI framework.

**Alternatives considered:** Typer (type-hint-driven, scales well to many
subcommands) and Click.

**Reasoning:** Phase 0's CLI has exactly two trivial, argument-free commands.
Adding a third-party dependency before there's real CLI complexity to justify
it would be over-engineering at this stage — `argparse` handles this with
zero dependencies. That said, the project's eventual CLI will grow to a
dozen-plus subcommands with typed arguments (`add-topic`, `add-source`,
etc.), and hand-written `argparse` subparsers get verbose at that scale.
**We'll revisit this explicitly in Phase 1**, when the first real subcommands
are added, with the Typer tradeoff laid out before switching — not before.

## pyproject.toml-only dependency management

**Decision:** Declare dependencies in `pyproject.toml` rather than
maintaining separate `requirements.txt` / `requirements-dev.txt` files.

**Alternatives considered:** `venv` + `requirements.txt` (the first option
listed in the original spec).

**Reasoning:** Two files can drift out of sync. `pyproject.toml` is a single
source of truth for both metadata and dependencies, and still uses nothing
beyond `pip` and `venv` — no extra tooling (Poetry, PDM) introduced. This is
the spec's "or another simple standard approach" option.

## Ruff for linting and formatting

**Decision:** Use Ruff for both linting and formatting instead of separate
Black + Flake8 + isort.

**Reasoning:** Ruff reimplements the rules of all three in one fast tool with
one config block. Three separate tools for the same outcome is more
configuration surface for no real benefit — the over-engineering we're
avoiding per rule 3.
