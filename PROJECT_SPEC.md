# Project Specification

## Purpose

Build a YouTube Content Operating System: a low-oversight, commercially
viable system that identifies opportunities, produces original videos,
publishes them through official YouTube APIs, tracks performance and revenue,
and uses data-driven experimentation to improve over time.

Primary platform: YouTube (Shorts first, then long-form).
Future platforms: Instagram and TikTok, as optional adapters only after
YouTube is proven reliable and profitable.

## Business goals

1. Build a functioning YouTube content business with realistic revenue
   potential. Business performance, reliability, compliance, and
   profitability are the primary measures of success.
2. Build a portfolio-quality software system. Technical quality matters
   because it enables long-term reliability, not as an end in itself.
3. Progressively reduce human oversight as operational evidence supports
   it, guided by explicit reliability, quality, compliance, and cost
   thresholds — not by schedule or convenience.

## Product principles

**YouTube-first.** YouTube is the source of truth for platform behaviour,
API availability, and analytics. Do not generalise abstractions prematurely
for hypothetical future platforms.

**Data-driven experimentation, not algorithm prediction.** The system does
not claim to guarantee views, clicks, or revenue. It proposes controlled
experiments, measures outcomes, and promotes winning patterns cautiously.

**Progressive oversight reduction.** Human approval is mandatory during
development, initial production, new-channel onboarding, new-format
onboarding, and after material system changes. A channel or workflow may
graduate to automated public publishing after meeting explicit reliability,
quality, compliance, and cost thresholds over a meaningful sample. Three
modes exist: manual approval, supervised automation, and qualified autonomous
publishing. Promotion between modes is explicit, reversible, and recorded.
High-risk content categories always retain human approval.

**Originality and quality over volume.** The system must track content
originality, source provenance, asset licensing, and disclosure requirements.
Repetitive, low-value, or reused content is an explicit failure mode.

**Profitability, not vanity metrics.** Every phase should improve at least
one of: content quality, operating efficiency, decision quality, audience
growth, revenue potential, or profitability. Upload volume is not a goal.

**Deterministic code where possible, LLM where genuinely needed.** Use LLMs
for language understanding, creative judgment, and qualitative analysis only.
Do not replace deterministic logic with agents.

## Explicit non-goals

This system is **not**:
- a spam or mass-upload network
- a copyright-scraping or unlicensed-reuse operation
- a platform-policy circumvention tool
- a system that claims to predict the algorithm or guarantee virality
- a system that scrapes platform data in violation of terms of service

## Target end state — full workflow

### Discovery and planning
1. Channel and niche configuration entered.
2. Trend, search-demand, and competitor signals gathered from permitted
   sources (YouTube Data API, Google Trends, manually supplied data, and
   approved third-party providers where needed).
3. Topics scored across opportunity, demand, and competition dimensions.
4. System recommends Shorts vs. long-form for each opportunity.
5. Duplicate-topic protection prevents retreading recent content.
6. Human selects or approves topics for production.

### Research and source management
7. Source material ingested (URLs, files, notes).
8. Claims extracted and mapped to sources.
9. Source quality scored; factual-risk flags raised.
10. Citation records preserved.

### Content generation
11. Content brief generated from topic, angle, and research.
12. Hook options generated.
13. Script generated with structured output and schema validation.
14. Script critiqued against a fixed rubric.
15. Human approves, rejects, or requests revision.
16. Title, description, tags, and YouTube metadata generated.
17. Originality check run before proceeding.

### Media production
18. Narration generated (approved TTS provider).
19. Captions generated.
20. Visual assets selected or generated from supported categories (owned
    media, public-domain, licensed stock, or licensed AI-generated visuals
    once that pipeline is implemented).
21. Scene manifest assembled.
22. Video rendered locally (FFmpeg).
23. Output validated (duration, resolution, audio levels, file integrity).
24. Shorts template applied.

### Publishing
25. Publishing mode checked for channel (manual approval / supervised
    automation / qualified autonomous).
26. Video uploaded privately/unlisted via YouTube Data API.
27. Scheduling and metadata submitted.
28. In manual or supervised mode: human explicitly approves public
    publication. In qualified autonomous mode: pre-publish checks run
    (quality, licence, duplicate, factual-risk, spending limit, daily
    limit) and video is published automatically if all pass; any failure
    halts and notifies.
29. Publication record stored with mode, checks passed, and operator.

### Analytics and business intelligence
30. YouTube Analytics API polled on schedule.
31. Performance metrics stored (views, impressions, CTR, retention,
    watch time, RPM, estimated revenue where available).
32. Production cost and API cost tracked per video.
33. Profit per video and channel-level profitability calculated.

### Experimentation and optimisation
34. Controlled experiments proposed (hook, title, length, posting time).
35. Sample-size safeguards enforced before declaring winners.
36. Winning patterns promoted cautiously; unprofitable formats retired.

### Reduced-oversight operation
37. Scheduling queues, approval notifications, circuit breakers.
38. Spending limits and automatic pausing when risk or cost thresholds
    are exceeded.
39. Audit logs for every autonomous action.
40. Channels graduate from manual → supervised → qualified autonomous
    publishing as evidence accumulates. Demotion is immediate and
    automatic if thresholds are breached.

## Human approval gates

Gates that are always required, regardless of publishing mode:
- Topic selection (can be pre-approved in bulk but not bypassed)
- Final script after critique
- Mode promotion (manual → supervised → qualified autonomous)
- Any content flagged as high-risk category

Gates that apply in manual and supervised modes only:
- Final pre-publish video review
- Explicit public publication approval

In qualified autonomous mode, these gates are replaced by automated
pre-publish checks (quality score, licence verification, duplicate check,
factual-risk threshold, daily/weekly publishing limits, spending limit).
Any check failure halts publication, triggers notification, and may demote
the channel's publishing mode. The operator always retains an immediate
manual kill switch.

## YouTube API data availability — known constraints

The following distinctions must be respected in the implementation:

| Signal | Source | Notes |
|---|---|---|
| Video metadata, search results | YouTube Data API v3 | Official; quota-limited |
| Channel statistics | YouTube Data API v3 | Public channels only |
| Competitor video details | YouTube Data API v3 | Public data |
| Own video analytics (views, CTR, retention) | YouTube Analytics API | Requires OAuth; channel owner only |
| Estimated revenue, RPM | YouTube Analytics API | Requires monetised channel |
| Shorts-specific retention metrics | YouTube Analytics API | Available for Shorts; confirm field names at implementation time |
| Search trend data | Google Trends (pytrends or official API) | Not a YouTube product; use carefully |
| Keyword search volume | No free official source | Requires third-party provider (e.g. SEMrush, Ahrefs) or manual input; never scrape |
| Competitor revenue | Not available | Do not attempt to infer |
| Algorithm ranking signals | Not available | Do not claim to model |

Scraping YouTube or Google in violation of terms of service is prohibited.
Any signal not available from an official or approved source must be flagged
as manually supplied or deferred until a permitted provider is integrated.

## Development standards

### Repository-first workflow

All source code is written directly to the local repository. Chat responses
are reserved for architecture decisions, implementation summaries,
verification steps, debugging, and explanations. Complete source files are
not printed into conversation unless explicitly requested. The repository —
not the conversation — is the source of truth.

### Incremental build rule

Each phase's code depends only on what is implemented and tested in prior
phases. Speculative infrastructure is not added until a phase requires it.

### Quality bar

Every phase must have: automated tests, passing lint, no placeholder code,
a demonstrable end-to-end capability, and a clear definition of done.

## Current phase

See `TASKS.md` for what is built vs. planned.
Phase 0 (environment) and Phase 1 (core data model) are complete.
Phase 2 (AI foundation) is next.
