# HiFly → Windy Fly Fork Architecture

## Overview

**HiFly** is the open-source AI agent framework (MIT license).
**Windy Fly** is a proprietary fork deeply integrated with the Windy ecosystem.

Think of it like Android AOSP (HiFly) vs Google Play Services (Windy Fly).
HiFly is a complete, usable agent. Windy Fly adds ecosystem identity and services.

---

## What Goes Into HiFly (Open Source, MIT)

Everything needed for a standalone, fully functional AI agent:

### Core Agent
- Agent loop (ReAct cycle with tool calling)
- LLM provider abstraction (OpenAI, Anthropic, xAI, Google, DeepSeek, Mistral)
- Memory system (SQLite + FTS5 + sqlite-vec)
- Personality system (10 sliders, presets, SOUL.md)
- Skills engine (sandbox, eval, promote, golden tests)
- Cost tracking + budget enforcement
- Offline mode (Ollama fallback)
- Sub-agent orchestration
- Shape-shifting (temporary personality reconfiguration)
- Cognitive decay + personality drift detection
- "Never Wrong Twice" failure detection + correction skills

### Tools
- Web search (Brave + DuckDuckGo)
- Fetch URL (read web pages)
- Weather (Open-Meteo, free, no API key)
- News (RSS feeds, no API key)
- Reminders + timers
- To-do list
- Calculator
- Unit conversion
- Coin flip / dice roll
- Calendar (Google Calendar OAuth, optional)

### Channels
- CLI chat
- Telegram
- Discord
- Slack
- WhatsApp (via Twilio)
- Signal
- Microsoft Teams
- IRC
- SMS (Twilio — dumb pipe, not owned identity)
- Email (SendGrid — dumb pipe, not owned inbox)

### Infrastructure
- Gateway (Bun/TypeScript, 50+ API endpoints)
- Dashboard (React + Vite + Tailwind, 8 pages)
- IPC bridge (UDS/TCP brain ↔ gateway)
- Docker deployment
- PyPI publishing
- Update system (check + auto-update)

### The IT'S ALIVE! Ceremony
**HARDCODED. NEVER REMOVED.** The hatching ceremony is DNA — every HiFly
descendant gets it. Lightning, fly ASCII art, mad scientist, "IT'S ALIVE!"
banner. This is the soul of the framework.

---

## What Goes Into HiFly WITH EPT GATING (per ADR-020 + ADR-025)

These integrations are **included in HiFly** but **require a valid Eternitas Personal Token (EPT)** at runtime. HiFly OSS forks can use them with proper credentials — Eternitas is the gatekeeper, not Windy Fly. This makes the Windy ecosystem services usable by any Eternitas-credentialed agent regardless of provenance, and gives Eternitas a single revocation surface that cascades across the comms diptych.

### Identity (EPT-gated)
- **Eternitas auto-registration at hatch** — passport (`ET26-XXXX-XXXX`) + EPT (365-day ES256 JWT) — included in HiFly, requires Eternitas API endpoint accessible to the fork
- Birth certificate PDF generation
- Neural fingerprint
- Trust score + clearance levels surfaced from Eternitas Integrity Index

### Communication (EPT-gated, per ADR-020 + ADR-025)
- **Windy Chat integration** — Matrix auto-provisioning on chat.windychat.ai; HiFly forks CAN provision accounts BUT only with valid EPTs from Eternitas (per ADR-020)
- **Windy Mail integration** — owned inbox @mail.windymail.ai; HiFly forks CAN provision mailboxes BUT only with valid EPTs from Eternitas (per ADR-025)
- **Windy Cell phone number** — number from Windy pool via Cell registry; EPT-gated provisioning (per ADR-017 Master Plan §C.2 once writes ship)

### Cloud (EPT-gated)
- Windy Cloud backup (encrypted to Cloudflare R2) — EPT-gated bucket access
- VPS deployment via Windy Cloud — EPT-gated
- Windy Pro API tools (recordings, translations, clone) — EPT-gated where applicable; Pro JWKS validation downstream of Eternitas

---

## What Stays EXCLUSIVE to Windy Fly (Proprietary, NOT in HiFly)

These are Windy-specific branding + UI that don't make sense outside the Windy ecosystem and are stripped during the HiFly fork generation:

### Ecosystem UI/UX (Windy-specific)
- "Born Into the Windy Ecosystem" panel
- Ecosystem status/health commands referencing Windy services by hardcoded name
- Default `[ecosystem]` config block pointing at Windy service URLs
- Dashboard Identity page UI (passport + trust score + QR code visualization)

### Branding
- "Windy Fly" name and 🪰 branding
- windyfly.ai domain references
- Windy ecosystem service URLs hardcoded as defaults

HiFly forks REPLACE these with generic placeholders + their own branding. HiFly's `[ecosystem]` block is empty by default; forks point it at any Eternitas-compatible service stack (Windy's, their own, or a competitor's).

---

## Fork Mechanics

### Preparing the Fork

Run `scripts/prepare-hifly-fork.sh` to generate a clean HiFly distribution:

1. Copies the repo to a new directory
2. Removes Windy-exclusive files
3. Renames "Windy Fly" → "HiFly" in user-facing strings
4. Renames CLI entry point: `windy` → `hifly`
5. Updates pyproject.toml: `name = "hifly"`
6. Replaces SOUL.md with generic HiFly personality
7. Keeps the IT'S ALIVE! ceremony (always)
8. Adds MIT LICENSE file
9. Result: standalone, MIT-licensed agent framework

### Upstream Flow

```
HiFly (open source) ──improvements──→ Windy Fly (proprietary)
                                          │
                                    Windy ecosystem
                                    integrations added
```

Generic improvements (better tools, improved agent loop, new channels)
are contributed upstream to HiFly. Windy-specific integrations stay
in the proprietary fork.
