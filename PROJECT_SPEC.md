# Project Specification

## Purpose

Build an AI-assisted content production system that researches topics,
generates original short-form video scripts, reviews them for quality and
factual accuracy, produces narration, assembles vertical video, creates
captions and metadata, uploads approved videos to social platforms, collects
performance data, and recommends future content improvements.

Initial target platform: YouTube Shorts. TikTok and Instagram may be added
later, only after the YouTube workflow is reliable.

## Goals

1. Build a legitimate content business that can eventually generate revenue.
2. Build a strong technical portfolio project demonstrating Python, APIs,
   databases, workflow automation, LLM integration, data analysis, testing,
   and responsible AI system design.

## Explicit non-goals

This system is **not**:
- a spam network or fake-engagement system
- a copyright-scraping or unlicensed-reuse operation
- a platform-policy circumvention tool

It prioritizes original value, lawful media usage, human review, platform
compliance, and long-term maintainability over speed of output.

## System workflow (target end state)

1. Topic entered manually, with intended angle.
2. Source material attached/referenced.
3. Content Creator generates a structured script.
4. Content Critic evaluates the script against a fixed rubric.
5. Human approves, rejects, or requests revision.
6. Narration generated.
7. Licensed/owned visual assets selected.
8. Scene manifest created.
9. FFmpeg renders a vertical video.
10. Rendered file validated.
11. Human performs final review.
12. Video uploaded privately/unlisted to YouTube.
13. Publication metadata stored.
14. Performance metrics collected over time.
15. Performance Analyst identifies patterns, proposes one controlled
    experiment at a time.

## Human approval gates

Required before: final script acceptance, video rendering (where
appropriate), and any publishing. Initial YouTube uploads are private or
unlisted only.

## Current phase

See `TASKS.md` for what's built vs. planned. As of this document, we are in
Phase 0 (planning & environment) — no business logic exists yet.

## Development standards

### Repository-first workflow

All source code is written directly to the local repository. Chat responses
are reserved for architecture decisions, implementation summaries,
verification steps, debugging, and explanations. Complete source files are
not printed into conversation unless explicitly requested. The repository —
not the conversation — is the source of truth.
