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

## What Stays EXCLUSIVE to Windy Fly (Proprietary)

Windy ecosystem integrations that require Windy services:

### Identity
- Eternitas auto-registration (passport on hatch)
- Birth certificate PDF generation
- Neural fingerprint
- Trust score + clearance levels

### Communication
- Windy Chat integration (Matrix auto-provisioning on chat.windyword.ai)
- Windy Mail integration (owned inbox @windymail.ai)
- Phone number from Windy pool (not Twilio direct)

### Ecosystem
- "Born Into the Windy Ecosystem" panel
- Ecosystem status/health commands
- Ecosystem URL configuration ([ecosystem] section)
- Dashboard Identity page (passport, trust score, QR code)

### Cloud
- Windy Cloud backup (encrypted to Cloudflare R2)
- VPS deployment via Windy Cloud
- Windy Pro API tools (recordings, translations, clone)

### Branding
- "Windy Fly" name and 🪰 branding
- windyfly.ai domain
- Windy ecosystem service URLs

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
