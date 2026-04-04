# 🪰 Windy Fly

**Your AI. Your Rules. Your Ecosystem.**

Windy Fly is a personal AI agent — a lifelong, self-improving companion that remembers everything, learns your preferences, and connects to the entire Windy ecosystem. Talk to it from your terminal, phone, browser, or any messaging platform.

```bash
pip install windyfly    # Install from PyPI
windy go                # Interactive setup + hatch ceremony
```

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

### From PyPI (Users)

```bash
pip install windyfly
windy go                   # Setup wizard
windy start                # Start agent + open dashboard
```

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
default_model = "gpt-4o-mini"

[personality]
preset = "buddy"          # buddy, engineer, powerhouse, coder, friend
warmth = 9
humor = 7

[costs]
daily_budget_usd = 5.0

[ecosystem]
eternitas_url = "https://api.eternitas.ai"
windy_mail_url = "https://api.windymail.ai"
matrix_homeserver = "https://chat.windypro.com"
windy_cloud_url = "https://cloud.windyfly.ai"
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

---

## Development

```bash
# Run tests (1014+)
uv run pytest tests/ -v

# Lint
uv run ruff check src/

# Build package
uv build

# Release (maintainers only)
./scripts/release.sh 0.6.0    # Bumps version, tags, pushes → PyPI
```

### Project Stats

- **1014+ tests** | 0 failures
- **22 LLM-callable tools**
- **101 DNA codons** | 100% implemented
- **8-page React dashboard**
- **9 messaging channels** supported

---

## License

Proprietary — (C) 2026 WindyLabs. All rights reserved.

---

*"If you have one copy of the DNA, you can recreate the entire organism."*
