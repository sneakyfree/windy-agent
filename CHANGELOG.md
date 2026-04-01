# Changelog

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
