# Multi-stage build for Windy Fly. Three goals over the Wave-1
# single-stage version:
#   1. Smaller runtime image (build deps don't ship)
#   2. Reproducible installs (locked .venv, no editable mode)
#   3. Version labels embedded so /version + GitHub Packages list
#      both show the live SHA and build timestamp
#
# Layout:
#   stage 1 (py-builder) : Python deps via uv → /app/.venv
#   stage 2 (runtime)    : slim base + the venv
#
# ONE process, no port: the agent answers in Windy Chat. The Bun gateway (port 3000, the local dashboard) left with
# the lean Windy Fly, so there is no Bun stage, nothing is EXPOSEd and there is no HTTP health check: the agent is
# PID 1, and the container is up exactly as long as the agent is.

# ── Stage 1: Python builder ────────────────────────────────────────
FROM python:3.12-slim AS py-builder

WORKDIR /app

# uv from upstream image — fastest pip alternative, deterministic.
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

# Install just the dep manifests first so this layer caches when
# only source changes. uv.lock makes the install reproducible.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-editable --no-install-project

# Then copy the project source and finalize the install.
COPY src/ src/
COPY README.md ./
RUN uv sync --locked --no-dev --no-editable

# ── Stage 2: Runtime ──────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Build args propagated as labels so a `docker inspect` reveals
# what's running. Also surfaced via /version when the image runs.
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG VERSION=dev

LABEL org.opencontainers.image.title="windy-fly"
LABEL org.opencontainers.image.description="Personal AI agent — Windy Fly runtime image"
LABEL org.opencontainers.image.source="https://github.com/sneakyfree/windy-agent"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.revision="${GIT_SHA}"
LABEL org.opencontainers.image.created="${BUILD_DATE}"

# Embed in env so the running container can self-report via the
# /version channel handler (PR #125).
ENV WINDY_BUILD_SHA="${GIT_SHA}"
ENV WINDY_BUILD_DATE="${BUILD_DATE}"
ENV WINDY_BUILD_VERSION="${VERSION}"

WORKDIR /app

# Runtime deps only. Don't carry build toolchain into the runtime image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pull only what we need from the builder.
COPY --from=py-builder /app/.venv /app/.venv
COPY src/ src/
COPY pyproject.toml README.md ./

# Make the venv's binaries the default PATH so 'windy' resolves
# without an explicit 'uv run'.
ENV PATH="/app/.venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1

RUN mkdir -p data

# The agent in the foreground as PID 1: one process, one channel (Windy Chat). It needs a hatched agent's passport
# (or a Matrix token) in its env; without one it says so and exits non-zero.
CMD ["python", "-m", "windyfly.main", "--channel", "matrix"]
