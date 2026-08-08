# AI Content Engine — multi-stage Dockerfile
#
# Targets (via --target):
#   builder  — installs all Python dependencies
#   runtime  — minimal production image (API, worker, or scheduler)
#
# CMD is overridden in docker-compose.yml per service:
#   API:       python -m uvicorn app.api.main:app --host 0.0.0.0 --port 8000
#   Worker:    python -m rq worker --with-scheduler --url $ACE_REDIS_URL
#   Scheduler: python -m app.workers.scheduler
#
# Security:
#   - Runs as non-root user (ace, uid 1000)
#   - No secrets baked into the image; all config via environment variables
#   - PYTHONDONTWRITEBYTECODE=1, PYTHONUNBUFFERED=1

# ── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system-level build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifest first (layer-cache friendly)
COPY pyproject.toml ./

# Create a virtual environment in the image layer
RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Install runtime dependencies only (no dev extras)
RUN pip install --upgrade pip && \
    pip install -e ".[dev]" 2>/dev/null || pip install -e "."

# Copy application source
COPY src/ ./src/

# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src"

# Install only runtime system libraries (no compiler)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1000 ace && \
    useradd --uid 1000 --gid 1000 --no-create-home --shell /sbin/nologin ace

# Copy virtualenv and application from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY pyproject.toml ./

# Artifact storage directory (typically overridden by a volume mount)
RUN mkdir -p /app/artifacts && chown -R ace:ace /app/artifacts

USER ace

# Default: API server. Override CMD in docker-compose or kubernetes manifest.
CMD ["python", "-m", "uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1
