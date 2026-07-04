# 🪰 Windy Fly

**Your AI. Your Rules. Your Ecosystem.**

Windy Fly is a personal AI agent — a lifelong, self-improving companion that remembers everything, learns your preferences, and connects to the entire Windy ecosystem. Talk to it from your terminal, phone, browser, or any messaging platform.

```bash
docker compose up -d    # The complete product — brain + dashboard
```

Or for developers, from a source checkout: `windy go` (interactive
setup + hatch ceremony). See [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md)
for which install path fits you — the `pip` wheel is the headless brain
only (no dashboard).

Your agent hatches, gets an identity, and starts chatting in under 2 minutes.

---

## Features

| Category | What It Does |
|----------|-------------|
| 🧠 **Memory** | Remembers every conversation, extracts facts, builds a knowledge graph |
| 🎛️ **Personality** | 10 sliders (humor, warmth, autonomy, etc.) — tune how your agent thinks and speaks |
| 🛠️ **17+ Tools** | Weather, reminders, to-dos, news, web search, calculator, unit converter, calendar |
| 💬 **Multi-Channel** | CLI, Matrix (Windy Chat), Telegram, Discord, Slack, WhatsApp, SMS, email |
| 🌐 **Dashboard** | React web UI at localhost:3000 — manage everything visually, no terminal needed |
| 🪪 **Identity** | Eternitas passport, birth certificate, trust score — your agent has a real identity |
| 💰 **Cost Control** | Daily/monthly budgets, per-model breakdown, automatic budget enforcement |
| 🔄 **Self-Improving** | "Never Wrong Twice" — logs corrections, creates correction skills, learns from mistakes |
| ☁️ **Cloud Backup** | Encrypted backups to Windy Cloud — memory survives device changes |
| 🖥️ **VPS Deploy** | `windy deploy --vps` — run your agent 24/7 on AWS |

---

## Quickstart

### From Source (Developers)

```bash
git clone https://github.com/sneakyfree/windy-agent && cd windy-agent
uv sync                    # Install dependencies
uv run windy go            # Interactive setup + hatch
```

### From PyPI (headless — no dashboard)

```bash
pip install windyfly
windy go                   # Setup wizard
windy start                # Start the agent (brain + channels only)
```

The wheel ships the Python brain and every channel adapter, but NOT
the gateway/dashboard (that's a Bun/TypeScript app that lives in the
source checkout and the Docker image). Want the full product without
a toolchain? Use Docker above. Details: docs/DISTRIBUTION.md.

### What Happens

1. **Setup** — paste your API key (OpenAI, Anthropic, xAI, or others)
2. **Hatch** — your agent comes alive with the "IT'S ALIVE!" ceremony
3. **Chat** — talk in terminal, or open the dashboard at `http://localhost:3000`
4. **Enjoy** — ask about the weather, set reminders, manage to-dos, search the web

---

## Commands

### Everyday

| Command | Description |
|---------|-------------|
| `windy chat` | Chat in the terminal |
| `windy start` | Start agent + gateway, open dashboard |
| `windy stop` | Stop all processes |
| `windy status` | Check what's running |

### Tools (via chat or slash commands)

| Command | Example |
|---------|---------|
| `/weather` | "What's the weather in Fort Anne?" |
| `/remind` | "Remind me to call Mom at 3pm" |
| `/todo` | "Add 'buy groceries' to my list" |
| `/news` | "What's the latest tech news?" |
| `/calendar` | "What's on my schedule today?" |

### Agent Management

| Command | Description |
|---------|-------------|
| `windy version` | Version + update check |
| `windy update` | Update to latest from PyPI |
| `windy doctor` | Diagnose issues |
| `windy ecosystem` | Check all service connections |
| `windy backup now` | Backup to Windy Cloud |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Dashboard (React + Vite + Tailwind)  ← localhost:3000      │
├─────────────────────────────────────────────────────────────┤
│  Gateway (Bun/TypeScript)  ← 50+ REST/WS API endpoints     │
├─────────────────────────────────────────────────────────────┤
│  Brain (Python 3.12+)  ← LLM orchestration, tools, memory  │
├─────────────────────────────────────────────────────────────┤
│  Memory (SQLite + FTS5 + sqlite-vec)  ← one file, zero ops  │
└─────────────────────────────────────────────────────────────┘
```

| Layer | Runtime | Role |
|-------|---------|------|
| **Dashboard** | React 19 + Vite + Tailwind | 8-page web UI for managing your agent |
| **Gateway** | Bun / TypeScript | API server, WebSocket chat, static files |
| **Brain** | Python 3.12+ | Agent loop, LLM calls, tools, personality |
| **Memory** | SQLite | Episodes, knowledge graph, skills, costs, reminders, todos |

---

## Configuration

All config lives in `windyfly.toml`:

```toml
[agent]
name = "Windy Fly"
default_model = "kimi-k2.5"

[personality]
preset = "buddy"          # buddy, engineer, powerhouse, coder, friend
warmth = 9
humor = 7

[costs]
daily_budget_usd = 5.0

[ecosystem]
eternitas_url = "https://api.eternitas.ai"
windy_mail_url = "https://api.windymail.ai"
matrix_homeserver = "https://chat.windychat.ai"
windy_cloud_url = "https://cloud.windycloud.com"
```

### Environment Variables

Secrets go in `.env` (never committed):

```bash
OPENAI_API_KEY=sk-...          # Required: at least one LLM key
ANTHROPIC_API_KEY=sk-ant-...   # Optional: for Claude models
BRAVE_SEARCH_API_KEY=...       # Optional: better web search (free at brave.com/search/api)
```

---

## Ecosystem

Windy Fly connects to the full Windy product suite:

| Service | What It Does | Status |
|---------|-------------|--------|
| **Eternitas** | Agent identity — passport, trust score | ✅ Integrated |
| **Windy Chat** | Matrix messaging via Synapse | ✅ Integrated |
| **Windy Mail** | Agent email inbox | ✅ Integrated |
| **Windy Cloud** | Backup storage, VPS deployment | ✅ Integrated |
| **Windy Pro** | Desktop/mobile app connections | ✅ Integrated |

Check connectivity: `windy ecosystem`

### Eternitas Trust Gate

Every sensitive action — `send_email`, `post_chat_message`, `run_command`,
`install_package`, `commit_push`, `upload_file` — is gated by the live
Eternitas Trust API before it hits the network.

**Contract:** `/Users/thewindstorm/eternitas/docs/trust-api.md` is the
authoritative consumer reference. Endpoint:
`GET {ETERNITAS_URL}/api/v1/trust/{passport_number}` — public, no auth,
100 req/min/IP, 5-min server cache. Responses carry
`status`, `band` (exceptional/good/fair/poor/critical),
`clearance_level`, `tier_multiplier`, `allowed_actions`, and
`cache_ttl_seconds`. Windy Fly mirrors the TTL in a local SQLite cache and
subscribes to `trust.changed` webhooks to flush on band flip or clearance
promotion.

**Env vars:**

| Var | Purpose | Default |
|-----|---------|---------|
| `ETERNITAS_URL` | Base URL for the Trust API | `http://localhost:8200` (dev) |
| `ETERNITAS_USE_MOCK` | `true` to skip live fetches and fail-open | unset |
| `WINDYFLY_TRUST_STRICT` | `1` to fail-closed when the trust service is unreachable | unset (fail-open) |
| `ETERNITAS_PASSPORT` | Agent's passport number; unset means "human/standalone, skip gate" | set during hatch |

**Agent-gate → Eternitas vocabulary:**

| Windy Fly gate action | Eternitas action |
|-----------------------|------------------|
| `send_email`, `post_chat_message`, `upload_file` | `send` |
| `run_command` | `execute` |
| `install_package` | `install_packages` |
| `commit_push` | `commit_push` |

**Running the live integration tests:**

```bash
# Terminal 1 — start Eternitas
cd /Users/thewindstorm/eternitas
./scripts/dev-start.sh

# Terminal 2 — run the live tests
cd /Users/thewindstorm/windy-agent
ETERNITAS_URL=http://localhost:8200 \
ETERNITAS_USE_MOCK=false \
WINDYFLY_TEST_PASSPORT_EXCEPTIONAL=<exceptional-band passport> \
WINDYFLY_TEST_PASSPORT_CRITICAL=<critical-band passport> \
uv run pytest tests/integration/test_trust_live.py -v
```

Tests skip automatically when the API isn't reachable.

---

## Development

```bash
# Run tests (~1100 offline + 27 live-Eternitas integration)
uv run pytest tests/ -v
uv run pytest tests/integration/ -v   # needs Eternitas at ETERNITAS_URL

# Lint
uv run ruff check src/

# Build package
uv build

# Release (maintainers only)
./scripts/release.sh 0.6.0    # Bumps version, tags, pushes → PyPI
```

### Project Stats

- **1100+ tests** | 0 failures (includes contract, integration, stress)
- **22 LLM-callable tools**
- **101 DNA codons** | 100% implemented
- **8-page React dashboard**
- **9 messaging channels** supported

---

## License

Proprietary — (C) 2026 WindyLabs. All rights reserved.

---

*"If you have one copy of the DNA, you can recreate the entire organism."*
