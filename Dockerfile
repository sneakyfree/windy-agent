# Multi-stage build for Windy Fly. Three goals over the Wave-1
# single-stage version:
#   1. Smaller runtime image (build deps don't ship)
#   2. Reproducible installs (locked .venv, no editable mode)
#   3. Version labels embedded so /version + GitHub Packages list
#      both show the live SHA and build timestamp
#
# Layout:
#   stage 1 (py-builder) : Python deps via uv → /app/.venv
#   stage 2 (bun-builder): gateway Node deps → /app/gateway/node_modules
#   stage 3 (runtime)    : slim base + copies of both stages

# ── Stage 1: Python builder ────────────────────────────────────────
FROM python:3.12-slim AS py-builder

WORKDIR /app

# uv from upstream image — fastest pip alternative, deterministic.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install just the dep manifests first so this layer caches when
# only source changes. uv.lock makes the install reproducible.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-editable --no-install-project

# Then copy the project source and finalize the install.
COPY src/ src/
COPY README.md ./
RUN uv sync --no-dev --no-editable

# ── Stage 2: Bun builder (gateway) ────────────────────────────────
FROM oven/bun:1-slim AS bun-builder

WORKDIR /app/gateway

COPY gateway/package.json gateway/bun.lock* ./
RUN bun install --production --frozen-lockfile 2>/dev/null \
    || bun install --production

# ── Stage 3: Runtime ──────────────────────────────────────────────
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

# Runtime deps: curl for healthcheck, Bun runtime to execute the
# gateway. Don't carry build toolchain into the runtime image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=oven/bun:1-slim /usr/local/bin/bun /usr/local/bin/bun

# Pull only what we need from each builder.
COPY --from=py-builder /app/.venv /app/.venv
COPY --from=bun-builder /app/gateway/node_modules /app/gateway/node_modules
COPY src/ src/
COPY gateway/ gateway/
COPY pyproject.toml README.md ./

# Make the venv's binaries the default PATH so 'windy' resolves
# without an explicit 'uv run'.
ENV PATH="/app/.venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1

RUN mkdir -p data

EXPOSE 3000

# Conservative health check — the gateway exposes /api/health when
# running. If gateway disabled, override CMD to skip.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=20s \
    CMD curl -f http://localhost:3000/api/health || exit 1

CMD ["windy", "start", "--daemon"]
