# 🪰 Windy Fly

**Your AI. Your Rules. Your Ecosystem.**

Windy Fly is the AI agent brain of the Windy ecosystem — a lifelong, self-improving, user-sovereign companion that lives inside Windy Chat and connects to every Windy product.

---

## What Is Windy Fly?

Windy Fly is a personal AI agent built from scratch to be:

- **A lifelong companion** — witty, opinionated, emotionally attuned, remembers everything
- **Self-improving** — "Never Wrong Twice" guarantee based on user feedback
- **User-sovereign** — your data, your rules, your agent. No silent overwrites.
- **Ecosystem-aware** — deeply integrated with Windy Pro, Windy Chat, Windy Cloud, and Windy Clone

---

## Architecture

Windy Fly uses a **dual-runtime architecture**:

| Layer | Runtime | Role |
|---|---|---|
| **Brain** | Python 3.12+ | Memory, reasoning, skill evolution, orchestration |
| **Gateway** | TypeScript / Bun | Channels, WebSockets, voice, devices, UI |
| **Memory** | SQLite + sqlite-vec + FTS5 | Single source of truth — one file, zero ops |
| **Chat Protocol** | Matrix (via Synapse) | Bot ID: `@windyfly:chat.windypro.com` |

---

## Project Status

> 🧬 **DNA Strand complete. Build begins at Phase 0.**

| Phase | Status | Deliverable |
|---|---|---|
| **0** | 🔜 Ready to build | Python agent loop + CLI + SQLite + personality |
| **1** | ⏳ Pending | Matrix bot in Windy Chat + Windy Pro API tools |
| **2** | ⏳ Pending | Soul Continuity + Control Panel + Truth Layer |
| **3** | ⏳ Pending | Skills Engine + Cost Ledger + Intent System |
| **4** | ⏳ Pending | Bun Gateway + Decay + Sub-Agents + Offline Mode |
| **5** | ⏳ Pending | Dashboard + Personality Versioning + Observability |

**Estimated timeline:** 18 weeks to v1.0.0

---

## DNA Strand Master Plan

The DNA Strand is the blueprint. Every build decision is already made. Every file is specified. Every function is specced. A model reads one codon, executes it, verifies it, moves to the next.

- [Part 1: Foundation + Phase 0](docs/WINDY_FLY_DNA_STRAND_PART1.md) — Repo structure, SQL schema, Python agent loop
- [Part 2: Phases 1–2](docs/WINDY_FLY_DNA_STRAND_PART2.md) — Matrix bot, Soul Continuity, Control Panel, Truth Layer
- [Part 3: Phases 3–4](docs/WINDY_FLY_DNA_STRAND_PART3.md) — Skills engine, cost ledger, intent system, emotional awareness, Bun gateway
- [Part 4: Phase 5 + Ecosystem Map](docs/WINDY_FLY_DNA_STRAND_PART4.md) — Dashboard, observability, master file index, codon checklist

**Total:** 59 codons · 47 source files · 6 phases · 18 weeks

---

## Research & Architecture Docs

The DNA Strand was synthesized from extensive research across 6 AI reviewers:

- [Synthesized Architecture](docs/architecture/synthesized_architecture.md) — Final canonical master plan from Grok, Gemini, ChatGPT, Perplexity ×2, and AntiGravity Opus 4.6
- [Greenfield Architecture Analysis](docs/research/greenfield_agent_architecture.md) — What a clean-slate agent improves over OpenClaw + Hermes
- [OpenClaw/Hermes Merger Analysis](docs/research/openclaw_hermes_merger_analysis.md) — Feasibility analysis of merging both architectures
- [Grok Analysis](docs/research/grok_plan_analysis.md) — Deep critique of the strategy session

---

## 5-Minute Quickstart

Get a fully working AI agent in 5 minutes:

```bash
# 1. Clone
git clone https://github.com/sneakyfree/windy-agent && cd windy-agent

# 2. Install
uv sync

# 3. Configure
cp .env.example .env && edit .env  # Add your OPENAI_API_KEY

# 4. Hatch — interactive setup wizard
uv run windy go

# 5. Chat — start talking to your agent
uv run windy chat

# 6. Status — check everything
uv run windy status

# 7. Doctor — diagnose issues
uv run windy doctor
```

### Additional Commands

| Command | Description |
|---|---|
| `uv run windy start` | Start brain + gateway (opens dashboard) |
| `uv run windy stop` | Stop all Windy Fly processes |
| `uv run windy test` | Self-test (verify agent works) |
| `uv run windy logs` | Tail brain/gateway logs |
| `uv run windy config show` | View current configuration |
| `uv run windy version` | Show version and environment info |

---

## Ecosystem Integration

```
Windy Fly ←→ Windy Chat (Matrix/Synapse)
Windy Fly ←→ Windy Pro Desktop (account-server API)
Windy Fly ←→ Windy Pro Mobile (same Matrix protocol)
Windy Fly ←→ Windy Cloud (distributed file storage)
```

---

## Part of the Windy Ecosystem

| Product | Repo | Role |
|---|---|---|
| Windy Pro | sneakyfree/windy-pro | Desktop app (Electron + Python) |
| Windy Pro Mobile | sneakyfree/windy-pro-mobile | Mobile app (React Native/Expo) |
| **Windy Fly** | **sneakyfree/windy-agent** | **AI agent brain** |
| Windy Cloud | sneakyfree/windy-pro-cloud | Distributed storage |
| Windy Chat | (via Synapse) | Matrix homeserver |

---

## License

Proprietary — © 2026 WindyLabs. All rights reserved.

---

*"If you have one copy of the DNA, you can recreate the entire organism."*
*This repo is that copy.*
