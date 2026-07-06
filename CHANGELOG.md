# Changelog

## 0.6.1

The keyless release (PRs #247–#256): 0.6.0 shipped hours before the
keyless grandma path landed, so the published package had none of it.
This release brings PyPI up to the runtime the ecosystem actually runs.

### Keyless / Windy Mind brain
- `windy go` option 1 = "Free — no key needed": keyless config, hatch,
  and launch with the Windy Mind free-compute brain (#248)
- Fly→Mind brain path is load-bearing: EPT captured + persisted at
  hatch, Mind responses translated to the loop's shape, Mind on the
  provider circuit breaker (#247); tool-bearing calls flow to Mind by
  default now that Mind's tool-calling is live (#254)
- Kiosk honesty: piped/no-TTY `windy go --keyless` no longer aborts the
  hatch on EOF, installs uv/bun itself, and reports truthfully when the
  free cloud brain could not be connected instead of claiming success
  (#256)

### One-soul chat identity
- The Fly logs into Windy Chat as its own `@agent_<passport>` identity
  minted from its Eternitas passport, with the hatch's DM room and the
  minted device id; the roster midwife yields while the real Fly holds
  the runtime claim (#252)
- Runtime claim accepts the agent's EPT as bearer (#253) and recovers
  the passport id from the EPT itself, so keyless agents actually claim
  their slot — no more double replies from midwife + Fly (#255)

### Reliability
- Matrix channel shuts down cleanly on SIGTERM: the sync loop is
  cancelled, presence goes offline, pending messages flush, and the
  runtime claim is released immediately (was: process hung until the
  supervisor SIGKILLed it and the claim orphaned for its full TTL) (#255)
- The grandma rescue kit (`/normal`, `/resurrect`, `/pause`, `/resume`,
  panic phrases) now works on the CLI channel too — previously the CLI
  bypassed the rescue layer and reported "Unknown command" for the exact
  commands the lifeboat banner suggests (#255)
- `--channel matrix` without credentials prints a friendly pointer to
  `windy go` instead of a raw traceback (#255)

### Provisioning contracts
- Agent provisions its own mailbox with its EPT; send-tool ambiguity
  fixed (#249)
- Cloud backup conforms to the canonical archive contract with
  AES-256-GCM (#250); `windy deploy --vps` uses the canonical
  deploy-fly contract (#251)

## 0.6.0

Ten weeks of merged work (PRs #151–#233) finally reaches the release
channel — PyPI had been frozen at 0.5.1 since April while master moved on.

### Stability ("tank" sprint, from the 2026-07-04 architecture audit)
- Recovery-layer portability: lifeboat/pause/guest/panic flags now default
  to `~/.windy` via `windy_state_dir()` instead of a hardcoded dev-box path
  (they silently did nothing on customer machines) — with a tripwire test
- Corrupt `windyfly.toml` boots on safe defaults + loud warning + `/status`
  notice instead of crash-looping under systemd
- Updates record a rollback version, verify the new install in a fresh
  interpreter, and auto-roll back on a broken release; `windy rollback`
  works with no argument; pre-release version strings compare correctly

### Channels
- Discord + Slack as first-class BYO-token channels; per-channel runtime
  claim so one agent can live on several channels at once
- Telegram menu curation (destructive commands hidden from autocomplete)

### Models
- Opus 4.8 support: temperature deprecation handled across the 4.7+ line,
  reasoning-depth wired to extended thinking, OAuth x-api-key collision fix

### Fixes
- `/lifeboat` "Since:" line rendered (wrong state key since May)
- WindyMailAdapter sends `body_text`; `ETERNITAS_URL` canonicalized
- Dashboard chat protocol + systemd status detection fixes


## 0.5.1

### Bug Fixes
- Fixed `matrix_provision.py` — missing `import logging` and `logger` caused NameError on provisioning failure
- Changed bare `except Exception:` to log warnings in matrix provisioning

### Tests
- Comprehensive E2E hatch test suite (`test_hatch_e2e.py`) — 30+ tests covering orchestrator flow, naming ceremony, birth certificate generation, retry/recovery, email PDF attachment, and SMS formatting

### Dead Code Cleanup
- Removed 6 unused integration stubs from `integrations/`: windy_word, windy_cloud, contact_discovery, windy_traveler, windy_clone, push_gateway
- Removed `test_integrations.py` (tested only dead stubs)
- Trimmed `test_hardening_integrations.py` to keep only live `tools/windy_api` and agent loop resilience tests

## 0.5.0

### Birth & Identity
- Naming ceremony — agent asks for its name after hatching
- Birth certificate shows hardware specs (CPU, RAM, GPU, OS)
- "Creator" label replaces "Owner" on birth certificate
- Birth certificate PDF attached to announcement email
- Post-hatch nudge to Windy Chat

### Daemon Mode
- `windy start --daemon` runs brain + gateway as fully detached background processes
- `windy go` defaults to daemon mode — agent survives terminal close
- macOS launchd service install (`windy install-service`)
- Linux systemd service install (`windy install-service`)

### Unified Command Registry
- 140 unified commands (108 core + 32 ecosystem-exclusive)
- Same commands work on terminal, Telegram, Discord, Slack, Matrix, WhatsApp, Signal, Teams, IRC
- Dangerous command gating (kill, reset, forget require confirmation)
- BotFather script for Telegram command autocomplete

### Channel Adapters
- 7 new adapters: Telegram, Discord, Slack, WhatsApp, Signal, Teams, IRC
- Matrix adapter refactored to extend ChannelAdapter
- Auto-detection from environment variables
- `windy channels` shows configured/unconfigured platforms

### Industrial-Grade CLI
- 35+ terminal commands: start/stop/restart/kill/ps, doctor/debug/logs, model/soul/budget, memory/skills, export/import/reset, repl
- PID file with key=value format (brain, gateway, started timestamp)
- `windy kill` escape hatch — always works even if everything else is broken

### Birth Announcement
- SMS via Twilio (with mock fallback) — "IT'S ALIVE!" template
- HTML email via Windy Mail with agent identity card
- Dashboard link for instant chat

### CI/CD
- GitHub Actions workflow: lint, type check, test (Python 3.12 + 3.13), build

## 0.4.0

- Unified command registry (117 commands)
- Channel adapter system
- Industrial-grade CLI

## 0.1.0

- Initial release
- ReAct agent loop with memory, personality, skills
- Matrix bot integration
- Eternitas passport system
- Birth certificate generation
