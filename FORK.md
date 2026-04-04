# HiFly → Windy Fly Fork Architecture

## Overview

**HiFly** is the open-source, provider-agnostic AI agent framework.
**Windy Fly** is a fork of HiFly, deeply integrated with the Windy ecosystem.

The fork is maintained as a downstream branch. HiFly improvements flow
into Windy Fly. Windy-specific features stay in the fork.

## Fork Boundary

### HiFly Core (generic, provider-agnostic)

Everything an AI agent needs to function independently:

```
src/windyfly/
├── agent/              # Reasoning loop, models, offline fallback
│   ├── loop.py         # ReAct agent loop
│   ├── models.py       # LLM provider abstraction
│   ├── offline.py      # Ollama fallback
│   ├── providers.py    # Multi-provider management
│   └── shape_shift.py  # Personality reconfiguration
├── bridge/             # IPC (UDS/TCP) brain ↔ gateway
├── channels/
│   ├── cli.py          # CLI chat interface
│   ├── sms.py          # Twilio SMS (generic)
│   └── email.py        # SendGrid email (generic)
├── memory/             # SQLite + FTS5 + sqlite-vec
├── personality/        # Sliders, presets, versioning
├── skills/             # Plugin system + evaluation
├── observability/      # Events, logging
├── soul_import/        # Soul Passport (import personality)
├── cli.py              # Unified CLI (windy go/init/start/stop/...)
├── commands.py         # doctor, update, logs, config, version
├── config.py           # TOML + env loader
├── control_panel.py    # Slider management
├── platform.py         # Cross-platform abstraction
├── quickstart.py       # windy go (zero-friction setup)
└── setup_wizard.py     # windy init (TUI wizard)

gateway/
├── src/server.ts       # Bun HTTP + WebSocket
├── src/bridge.ts       # IPC client (UDS/TCP)
├── src/websocket.ts    # Chat WebSocket handler
├── src/providers.ts    # Provider config UI backend
├── src/machines.ts     # Mission Control
└── public/
    ├── index.html      # Trust Dashboard
    └── setup.html      # Browser setup wizard

scripts/
└── install.sh          # curl | bash bootstrap
```

### Windy Fly Fork (ecosystem-specific)

Files that only exist in the Windy Fly fork:

```
src/windyfly/
├── channels/
│   └── matrix_bot.py   # Windy Chat (Matrix) bot — hardwired
├── tools/
│   └── windy_api.py    # Windy Pro API tools (translate, recordings, clone)
├── hatching.py          # "IT'S ALIVE!" ceremony + Born Into display
└── matrix_provision.py  # Auto-provision Matrix bot on chat.windyword.ai

SOUL.md                  # Windy Fly's specific identity/soul
RIBOSOME_BLUEPRINT.md    # Windy ecosystem integration blueprint
```

### The "It's Alive!" Ceremony — CORE HIFLY (non-negotiable)

The hatching ceremony is **hardcoded into HiFly core**.  Every descendant —
HiFly, Windy Fly, or any future fork — gets the full ceremony:

- Lightning bolts
- The fly emerges
- Mad scientist: "IT'S ALIVE!!! THE FLY IS ALIVE!!!"
- Giant ASCII banner
- Audio hook (if sound file present)

This is the framework's signature.  Like the Linux penguin or the Mac
startup chime.  It stays forever.  It cannot be disabled.

### Configuration Differences

| Setting | HiFly | Windy Fly |
|---------|-------|-----------|
| Hatching ceremony | "IT'S ALIVE!" (same) | "IT'S ALIVE!" (same) |
| Post-hatch status | "HiFly Status" panel | "Born Into the Windy Ecosystem" panel |
| `[matrix]` section | Optional | Pre-configured for chat.windyword.ai |
| `MATRIX_BOT_*` env vars | User-supplied | Auto-provisioned |
| `WINDY_API_URL` | Not present | http://localhost:8098 |
| `WINDY_JWT` | Not present | Auto-obtained |
| Default bot user | None | @windyfly:chat.windyword.ai |

## How to Maintain the Fork

1. **HiFly** lives on `main` branch of the `hifly` repo
2. **Windy Fly** is a fork repo with its own `main`
3. Periodically merge upstream HiFly changes into Windy Fly
4. Windy-specific files (listed above) never go upstream
5. The fork boundary is enforced by this document

## Renaming Guide

When creating the HiFly repo from this codebase:

1. Rename package: `windyfly` → `hifly`
2. Rename CLI command: `windy` → `hifly`
3. Remove Windy-specific files (matrix_bot.py, windy_api.py, matrix_provision.py)
4. **KEEP hatching.py** — the ceremony is core HiFly DNA
5. **KEEP branding.py** — set `HIFLY_BRAND=hifly` as default
6. Remove SOUL.md, RIBOSOME_BLUEPRINT.md
7. Replace with generic SOUL.md template
8. Update pyproject.toml name, description
9. Update install.sh URLs
10. Remove Windy Chat default config from windyfly.toml template
