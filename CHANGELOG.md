# Changelog

## Unreleased (0.7.2)

**Terminal hatching now signs in with your Windy account.** Eternitas is
closing the anonymous hatch door (`/bots/auto-hatch` will require a
credential), so hatching from the terminal needs the owner's Windy sign-in.

- New `windy login` (browser sign-in via loopback + PKCE; works over SSH
  with the printed link and a port forward), `windy logout`, and
  `windy whoami`. The sign-in is stored in `~/.windy/hub_session.json`
  (mode 0600) and refreshed automatically.
- An interactive `windy go` / hatch asks you to sign in when it needs to.
  Non-interactive hatches fail with a clear "run `windy login`" message
  instead of a bare 401.
- auto-hatch credential order: the owner's hub token always wins —
  `WINDY_HUB_JWT` → the `windy login` session → `ETERNITAS_OPERATOR_JWT`
  (only when there is no hub token). Eternitas binds the owner from a hub
  token; an operator JWT would file the agent under a placeholder operator.
- Identity link-back uses the `windy_identity_id` claim and never falls
  back to `sub` on a hub login token (there `sub` is a different id).
- Agents that are already hatched are unaffected: nothing re-hatches or
  asks you to sign in at startup.
- Agents now renew their Eternitas passport token automatically (at boot
  and daily; or run `windy ept refresh`), using Eternitas's new
  self-refresh endpoint. A token is renewed with its own still-valid EPT,
  or, once expired, with your `windy login`. This also picks up new
  claims (e.g. the link to your Windy account) without a re-hatch. For
  systemd installs, set `WINDY_ENV_FILE` to your unit's `EnvironmentFile`
  so the renewed token survives restarts.

- **Web search works on real installs again.** The Windy Search client read
  `WINDY_PASSPORT_EPT` and required `WINDY_SEARCH_BASE_URL`, but hatch and
  `windy ept refresh` store the passport token in `ETERNITAS_PASSPORT_TOKEN`.
  So `web_search`/`fetch_url` refused every call. It now reads
  `ETERNITAS_PASSPORT_TOKEN` (the old name still works), defaults to
  https://api.windysearch.com, uses the canonical `POST /v1/search`, explains
  401/429/503 plainly, and stops calling until the 1st after the monthly
  budget runs out (instead of retrying a 429 all month).
- New `windy deregister [--passport …] [--yes]`: the owner permanently revokes
  the agent's Eternitas passport (owner's Windy sign-in → Eternitas operator
  session → revoke, with the full revocation cascade). It asks y/N (default
  No), refuses non-interactively without `--yes`, and afterwards comments out
  the dead `ETERNITAS_PASSPORT_TOKEN` line in the agent's env file (atomic,
  with a backup). Memory and files are left alone.

**First run, fixed end to end** (from a clean-machine test of `pip install
windyfly` → `windy login` → `windy go` → chat → `windy ept refresh`):

- **A fresh `windy go` now gets a real passport.** With no `ETERNITAS_URL`
  set, the hatch used to have no issuer and gave up. It now defaults to
  https://api.eternitas.ai and writes `ETERNITAS_URL` into the generated
  `.env`. Set `ETERNITAS_URL` to use a different issuer, or `ETERNITAS_URL=off`
  to switch Eternitas off.
- **One agent, one passport.** The hatch now saves the passport number
  (`ETERNITAS_PASSPORT`) next to its token. Re-running `windy go` keeps both
  instead of blanking them, and won't hatch an agent that already has a
  passport; `windy go --force` (or `windy deregister`) starts over.
  Previously a re-run minted a second passport.
- **No more ✓ for things that didn't happen.** "✓ uv installed" only prints
  when uv really is installed (the installer pipe now uses `pipefail`).
  A pip-installed Windy Fly doesn't need uv or bun at all and starts with the
  current Python. The local mock mail server only runs with the explicit
  dev/test opt-in, and a placeholder inbox or phone is shown as
  "not set up yet" rather than "✓". A real passport is no longer labelled
  "(local)".
- **You can chat right after hatching.**
  - `windy go` ends with the exact command: `windy stop && windy chat`.
  - The "already running" refusal says the same thing.
  - `windy stop` now waits for the agent to exit and releases its runtime
    slot, so the next `windy chat` isn't refused as "already hosting this
    agent". An exited-but-unreaped (zombie) process no longer counts as
    running, which made `windy stop` sit out its whole timeout in containers.
  - `/quit`, `/exit` and Ctrl-D leave the chat.
  - INFO logs go to `data/cli.log` instead of scrolling through the
    conversation.
- **`windy ept refresh` reports every outcome honestly.** It reads the same
  env file the refresh writes (`WINDY_ENV_FILE`, else the project `.env`).
  A token Eternitas keeps (`reissued: false`, or any status this version
  doesn't know yet) is reported as "kept — still valid". Only an HTTP error or
  refusal, or an unreachable issuer, prints "Refresh failed", and it says
  which. (It used to print "Refresh failed (None)".)
- `windy --version` works (same output as `windy version`). Help text and the
  guided Anthropic signup now use a current model instead of the retired
  `claude-3-5-sonnet-latest`, and the Anthropic key check no longer rejects
  good keys by probing a retired model. Chat's "never provisioned" hint names
  `windy go` (there is no `windy hatch`).

## 0.7.1

**Action required: set your Telegram owner explicitly.** Through 0.7.0,
an install with no `AGENT_OWNER_TELEGRAM_ID` silently defaulted its
Telegram allowlist to one maintainer's personal account. That account
was treated as the owner of your agent, and your own messages were
dropped. The default is gone. Set `AGENT_OWNER_TELEGRAM_ID=<your
numeric Telegram id>` (or `WINDY_OWNER_IDS="telegram:<id>"`) in your
env. If you leave it unset, the first person to message the bot is
bound as its owner (trust-on-first-use, as on every other channel), so
message it yourself first.

Also since 0.7.0:
- Telegram: a rejected bot token is no longer written to the log and
  event ledger in full (#364)
- Liveness probe: no longer restarts the agent every 15 min when
  Telegram rejects the token, since a restart can't fix that (#364)
- Auth: fixed a race that could drop a turn to the local lifeboat model
  under concurrency (#363)
- Context gauge: no longer drains N× on an N-tool turn (#362)
- Native web search re-enabled on claude-opus-5; voice replies work
  again (#361)
- Gateway: tests no longer bind :3000 on import (#365)

## 0.7.0

Ten weeks of work that never reached the wheel. 0.6.1 shipped
2026-07-06; master ran 108 commits past it (PRs #259–#358) while
`windy update` told every installed copy it was already current.

By 2026-08-11 that had stopped being a packaging problem: #353 had to
warn, in the README and in `docs/DISTRIBUTION.md`, that `pip install
windyfly` shipped a client which could mint a **second passport** for
an agent that already held one, and 401'd on a fresh machine with no
operator key — because the published wheel predated the
adopt-don't-mint guard (#345) and the terminal door's move onto the
consumer endpoint (#349). Both warnings said the fix was "a release
≥ 0.7.0". This is that release; the warnings are removed with it.

Also here: two new subsystems (MCP, the cross-platform supervisor), the
Chronicle memory plane, and a security pass that closed real holes.

### MCP — both directions (ADR-060)
- Native MCP control server over the Capability Plane (#282), later
  widened to expose the FULL plane (#286)
- `mcp.*` client — the Fly consumes external MCP servers, Fly-as-doctor
  (Route C) (#285)

### Supervisor — the timer zoo is gone (Tiers 1–3)
- Cross-platform guardian sidecar + heartbeat file (#318); OS keep-alive
  backends + guardian entry point (#319)
- In-process maintenance scheduler replaces the per-job systemd timers
  (#320); continuity battery folded into it (#323)
- `install-service` now installs the supervisor (#322)
- Retry-once on a torn heartbeat read (#321); launchd `gui/user` domain
  fallback + XML escaping (#325)

### Memory — the Chronicle plane
- `memory.search` + `memory.read_range`: the model gets the key to its
  own past (#313)
- The dated Journal index over the Chronicle (#314), with its own node
  type so it stops colliding with the reflective diary (#315)
- Decay may dim, never erase — Doctrine Law 1 (#312)
- The agent reads its own history when it wakes up (#336)
- Semantic recall was dead, then fatal, then near-pointless — fixed
  (#330); `soul_history` bloat capped with retention + VACUUM (#271);
  migration 11 no longer crashes a concurrent open (#343); recall stops
  dropping the one word that identified the memory (#341)

### Security
- Agents owner-bind by default; owner PII and tooling band-gated from
  strangers (#265)
- Taint model closes injected-content → exfil/RCE (#280)
- Inbound SMS + email resolve a real sender band instead of OWNER (#279)
- SSRF guard on `fetch_url`'s direct-httpx fallback (#278)
- Capability deny-list matches Windows separators too — `\` used to walk
  straight past it (#311)
- Nothing in the registry required OWNER, so TRUSTED bought `shell.exec`
  (#344)
- Bridge: SO_PEERCRED doorman + off-thread dispatch (#305); a non-object
  JSON frame gets a structured error, not a silent disconnect (#297)

### Recovery, and proving it works
- Weekly fire drill — the agent rehearses its own death (#303), robust
  to a pending systemd job (#308)
- Weekly continuity score — Principle #7 as a number (#307)
- Chaos harness: kill it, break its world (#334), runnable on Windows
  (#335)
- Auth survival: self-heal a mid-run OAuth rotation (#262), unfreeze
  rotated tokens (#276), OAuth-aware paid-health probe so recovery is
  possible on Max-plan boxes (#291), and an auth-dead agent keeps a
  local brain instead of "try again later" (#296)
- Probe-heal watches the real per-channel units — the heal path had been
  dead 15h (#294)
- Turnover letters on graceful shutdown, not just `/new` (#301)
- Engine transparency: the serving model shows in the gas-tank panel
  (#298); rescue-kit menu cuts 91 commands to the 15 that matter when
  the brain is broken (#302); welcome-spam race latched (#300)

### Channels
- Signal, IRC and Teams are launchable; Teams silent-drop send fixed
  (#273)
- Matrix: real identity resolved from the access token — Windy 0 had
  been dark on Windy Chat (#260); `/sync` backoff, honest heartbeat, and
  it joins the hatch DM room (#357)
- Mail inbox watch — the agent notices mail sent to it (#358)
- `/remember` actually persists on Matrix (#259)

### Hands
- `windycode` via the Agent Bus (#275) and `windycode_web` in the browser
  builder (#287, #288, base64 on the wire #289)
- `windy_domains` + `windy_sites` on the Cloud cells (#283), following
  redirects through nginx trailing-slash routing (#284)
- `fetch_url` renders JS pages via windy-search Browserbase (#266)
- The agent turns the dials on the user's Windy Word app (#263) and
  carries its per-install control token (#281)
- The voice path gets the tool registry — grandma can DO things by voice
  (#292)

### Identity / hatch
- Eternitas issues the signed certificate of record; the local mint is
  retired (ADR-064) (#293), footer overprint fixed (#290)
- The terminal door hatches through the consumer door (#349); the browser
  door no longer mints a second passport and orphans the first (#345)
- An unconfigured run no longer invents a passport and calls it success
  (#346)
- Revocation survives an Eternitas outage (#353); two invented revocation
  receivers dropped (#354)

### Portability
- Every file read and write in `src/` names its encoding (#332); tests
  too (#324) — the cp1252 landmine
- The skill sandbox was dead on Windows; `stop()` dropped memory
  silently (#333)
- `os.replace` for the offline queue (#310)
- start/stop found the pid file relative to CWD (#339); commands
  silently operated on the wrong folder when run from elsewhere (#342)
- An optional extra could crash the agent on Intel Macs (#340)

### Models
- `claude-opus-5` in the catalog — the patch Windy 0 had run uncommitted
  since 2026-08-18 (#356); `claude-opus-4-8` (#261)
- Mind retries model-less on 422, so catalog drift stops knocking agents
  off their primary brain (#295)

### Personality
- Raw-model mode, and adaptive-mode deprecated (#316); raw mode is the
  default on purpose, not by accident (#331)
- Steering → substrate: hardcoded emotion injections and fs/shell
  keyword nudges retired in favor of self-descriptions (#304)

### Telemetry
- `llm.call` events to Windy Admin — the last un-ledgered burn point
  (ADR-WA-001) (#274), with real `duration_ms` (#277)

### Housekeeping
- CI moved to the self-hosted Kit 0 runner (#328), with uv pinned so
  `setup-uv` skips the forge releases API (#355)
- Test suite stopped reading the machine's real credentials (#338) and
  leaking a resident `.env` (#309, #317); WriteQueue leak that reddened
  master fixed (#329)
- The suite can no longer take a live agent's systemd unit down: the
  guard now covers `systemctl_stop`, the `os.system` `pkill`
  fall-throughs, and `subprocess.run` itself, so an unenumerated caller
  is caught too (#359)
- Post-AWS-exit dead weight deleted (#306)

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
