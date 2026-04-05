FROM python:3.12-slim

WORKDIR /app

# Install uv and bun
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip && \
    curl -fsSL https://bun.sh/install | bash && \
    ln -s /root/.bun/bin/bun /usr/local/bin/bun && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps (cached layer)
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-editable

# Install gateway deps
COPY gateway/package.json gateway/bun.lock* gateway/
RUN cd gateway && bun install --production 2>/dev/null || true

# Copy source
COPY . .

# Create data directory
RUN mkdir -p data

# Expose gateway port
EXPOSE 3000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:3000/api/health || exit 1

# Start agent brain + gateway
CMD ["uv", "run", "windy", "start", "--daemon"]
