# Changelog

## Unreleased (0.7.4)

- **Short-lived, key-bound service tokens (Eternitas mode B).**
  `agent_keys.request_agent_token(aud)` trades a DPoP proof by the agent's
  registered key for a ≤5-minute `EPT+agent` scoped to one Windy service
  (`windy-calendar`, `windy-mail`, …), bound to the key (`cnf.jkt`) and
  carrying the agent's live Integrity Index. No bearer is sent, so a leaked
  EPT can't mint one. Tokens are cached per service until 30 s before expiry,
  refusals raise `AgentTokenError` with Eternitas's code (and `retry_after`
  on 429), and a token bound to any other key is rejected. `service_dpop()`
  makes the per-request proof that services require for writes.

- **Tool calls through Windy Mind reach the model you asked for.** Tool names
  like `vision.describe` are made safe for every provider behind Mind (letters,
  digits, `_`, `-`, at most 64 characters) and mapped back in the reply, and
  Anthropic-only server tools are no longer sent to Mind. Before, a full toolset
  made every named provider refuse the request, and Mind answered on a small
  local model instead of the one the agent asked for.

## 0.7.3

- **A busy Windy Mind no longer drops the agent into the lifeboat with the wrong
  reason.** When Mind answers 429/502/503/504 (busy: a free quota spent, a lane
  down), the agent waits (Retry-After, else 15 s, at most 30 s) and retries once.
  If Mind is still busy, the lifeboat notice now says "Windy Mind is busy right
  now" instead of blaming a missing credential (a Mind-routed agent has no
  direct key by design, so the fallback chain always reported `no-key`).

- **A terminal hatch through Eternitas's auto-hatch door sends `X-Windy-Hatch-Id`**
  (a fresh uuid4 per hatch attempt; Eternitas #185), so Windy Admin can join one
  birth across services. It's an opaque id and carries nothing about the owner.
  The hub ceremony (the default `windy go`) already stamps its own session id.

- **`windy go` opens the one hub hatch ceremony (ADR-059) — now the default.**
  `windy go` creates a hatch ticket at the hub (nothing is minted), prints
  "Open this to hatch your agent: <link> (code ABCD-1234)", opens your browser
  and waits while you check the code, name your agent and hatch it on the
  ceremony page. At "It's alive!" it prints "Say hi: <Windy Chat link>". The
  agent is born and lives in the cloud: nothing is written to `.env`, and this
  machine only remembers it (0600, beside your Windy sign-in), so the next
  `windy go` says you already have it. Ctrl-C cancels the ceremony at the hub.
  If your account already has an agent, `windy go` names it and offers to use
  it (never mints another). `windy bring-home`, to run the agent on this
  machine, is a placeholder until the hub's handover ships.
  **Opt-out for one release:** `WINDY_HATCH_VIA_HUB=0` runs the old terminal
  hatch, with a notice that it goes away in the next release.

- **Cost accounting is honest now.** Claude models were missing from the
  price table, and an unknown model fell back to gpt-4o-mini's price, so
  claude-opus-5 was billed at $0.15/M input: Windy 0's ledger read $0.14
  for about $4.70 of list-price work.
  - **Real prices:** Anthropic's current rate card for Opus 5 / 5.5 / 4.8,
    Fable 5 / 5.1, Sonnet 5 and Haiku 4.5, including cache writes (5 min
    and 1 h) and cache reads, which Anthropic reports separately from
    `input_tokens`. The longest matching model id wins.
  - **Never a guess:** an unknown model, or an unpublished price for a
    token class the call used, records the cost as unknown (NULL; no
    `cost_microcents` on the telemetry row) rather than a made-up number.
  - **Every call, once:** the ledger gets one row per LLM call, recorded
    inside `call_llm` for every path (turn rounds, retries, the voice
    bridge, intent/journal/goal/sub-agent helpers). Failed calls get a row
    too, with an error code (`rate_limited`, `auth`, `provider_http`,
    `timeout`, `network`, `no_provider`, `internal`). It used to be one
    row per turn from the loop only, with no failures and no `request_id`.
  - **Billing:** each row says `max_subscription` (list-price equivalent on
    a Max plan; the marginal cost is $0), `metered` or `local`.
  - Admin `llm.call` telemetry is now per call as well (successful calls).

- **Field health telemetry, disclosed and switchable off.** New
  `service.boot`, a 15-minute `service.health` (turn, tool, 429 and lifeboat
  counts, p95 turn time, and how many of our own rows the ingest quarantined
  or we dropped), `agent.run_failed` (one per turn where the human got no
  real answer, with a declared code), and `agent.model_demoted` (once per
  switch to a weaker or local model: the silent `llama3.2:3b` demotion is now
  a row).
  - Rows are validated against the enums declared at Windy Admin before they
    leave; an invalid row is dropped and counted, never sent to be
    quarantined. A 202 that quarantines anyway is logged as a warning.
  - **Customer installs send via the agent's own passport token.** Sources,
    in order: the fleet's `WINDY_ADMIN_INGEST_TOKEN`, then
    `WINDY_TELEMETRY_CLIENT_TOKEN` (empty by default; no token is built into
    the package), then the agent's EPT (default on; windy-admin #294 verifies
    it and pins rows to that passport). `WINDY_TELEMETRY_EPT_AUTH=0` turns the
    EPT path off. An agent with no passport sends and counts nothing.
  - Rows are **batched**: flushed at most every ~10 s, 100 per request (the
    ingest's cap), plus a best-effort flush at exit, well under the 30
    requests/min per passport limit.
  - A **429** waits out `Retry-After` (default 60 s) and keeps a bounded
    buffer (oldest dropped and counted); it never trips the breaker. A 413
    splits the batch and logs a warning.
  - A **401** for an expired EPT or an unpublished key renews the EPT once
    and retries. A 401 that persists, a revoked passport, an issuer mismatch
    or a 403 trips the breaker: one warning, no more sends this run, and a
    24h marker in `~/.windy` (0600) across restarts.
  - **Disclosed before anything is sent.** One line says what's sent at the
    end of a successful hatch and at the first `windy login` / `windy go`,
    and writes a marker in `~/.windy`. The passport and client-token paths
    stay closed until that marker exists (or `WINDY_TELEMETRY=1` is set, or
    someone runs `windy telemetry status|on`); before that nothing is built
    or counted. The fleet's own emitter token is exempt.
  - New `windy telemetry status|on|off`: the credential in use, whether the
    line was shown, and whether sending is paused; on/off saves the
    preference. `WINDY_TELEMETRY=0` still turns everything off. See
    docs/PRIVACY.md.
  - `WINDY_SYNTHETIC=1` (our probes only) marks rows `synthetic` and adds
    `X-Windy-Synthetic: 1` to requests to Windy hosts only (never third parties). The fire drill and the
    continuity battery mark their rows synthetic and warn on a quarantine.

- **`windy go` no longer crashes without a terminal.** Run from a script, CI
  or `docker exec` without `-i`, an already set-up `windy go` died with an
  `EOFError` traceback at "Already set up! Launch Windy Fly?". Every setup
  prompt now takes its stated default when stdin is at EOF, and says so in one
  line. (Found by the 0.7.2.1 clean-machine proof.)
- **No brain claims for a dead passport.** After `windy deregister` (or a
  suspension), `windy go --keyless` no longer says the agent is "powered by
  your agent's Windy passport" / "Windy Mind brain", and plain `windy go` no
  longer says "Windy Mind (free, keyless) configured". Both say the passport is
  revoked (start over with `windy go --force`) or suspended (reversible: the
  agent keeps its identity; no `--force`).

- **Consent for messages to other people** (legal review, 2026-09-23).
  - **Texts:** the first text to any new number now needs the owner's yes. `send_sms` returns `confirm_required` with a question the agent must relay word for word, and only `confirm_sms` (single-use, 10 minutes, bound to the exact text) sends it and remembers the number.
  - Every text ends with "— <agent>, AI assistant for <owner>. Reply STOP to opt out."; a `recipient_opted_out` answer is final and never retried.
  - **SMS itself stays off** until a sender that enforces STOP exists: without one, the tool says "SMS isn't available yet for Windy Fly agents."
  - **Emails:** every email now ends "Sent by <agent>, an AI agent acting for <owner>.", and the Resend path adds `X-Windy-Agent: <passport>`.

- **Texting, calls and phone numbers are parked until after launch.** `send_sms`
  says "Texting isn't available yet; I can reach them by email or you can
  message them in Windy Chat.", `make_call` says the same for calls, and the
  hatch no longer assigns a phone number: it could otherwise BUY a Twilio
  number on the owner's own account, or hand out a fake +1555 mock.
  (`WINDY_ENABLE_PHONE_PROVISION=1` re-enables it for development.)

**Every agent gets its own signing key (Eternitas agent-keys v1).** A
passport number shows that a passport exists; a key shows that the caller
holds it. See `docs/AGENT_KEYS.md`.

- The agent generates an ES256 (P-256) key pair on its own machine and
  registers only the public key with Eternitas, with a proof of possession.
  The private key never leaves the device and is not in logs or cloud
  backups.
- One credentials file per agent: `WINDY_CREDENTIALS_FILE`, otherwise
  `<state dir>/credentials.json`. It is mode 0600, written atomically, keeps
  a timestamped backup, and leaves other keys in the file alone.
- Registration runs as a background boot step (`eternitas.agent_keys`), a
  daily job, after `windy login`, and at the end of a successful hatch. Until
  Eternitas ships the routes it logs one INFO line and does nothing else.
- New `windy agent-key status|rotate|reset|revoke`. `reset` is the owner's
  recovery for a lost key: it runs a fresh browser sign-in (`prompt=login`),
  registers a new key (`reason: recovery`, or `--reason handover` when moving
  the agent), and revokes the old ones. Eternitas allows 2 per passport per
  24h. (`windy keys` stays the wk_ bot credential.)
- **EPT refresh by key.** Once the agent has a registered key, the EPT refresh
  first proves itself with an `Eternitas-Agent-Proof` header signed by that
  key. It needs no bearer, so it works even after the EPT has lapsed. The
  agent's own EPT and the owner's `windy login` session remain the fallbacks.
- Eternitas error codes (`detail.code`, e.g. `too_many_active_keys`,
  `stale_auth_time`, `owner_registration_limit`) are shown as they are.
- `sign_artifact()` returns a detached JWS carrying `kid` and `passport`,
  for Windy Drops signed publish.
- `hub_login.login()` gains `reauth=` (sends `prompt=login` and `max_age=0`)
  and `store=` (return the token without saving the session).

- **`windy bring-home` — move your cloud agent onto this machine** (ADR-059,
  hub handover AGENT_HANDOVER.md §9). One fresh sign-in; the agent's own
  ES256 key is registered at Eternitas on the owner path (`reason:
  handover`, old keys untouched so a failed handover leaves the cloud agent
  working, and the new key is revoked). The hub hands over Windy Chat and
  Windy Mail credentials in a one-time pickup, which is sealed to a 0600 file
  the moment it arrives, so a crash never loses it (re-running resumes; after
  the pickup is gone the same key starts a rotate round). The agent's memory
  (`ndjson-v1`) is checked against its sha256/event count, kept verbatim and
  imported as episodes; its turnover letter is the first thing the local
  agent reads. The passport token comes from Eternitas by the new key's
  proof. The body gets the keyless Windy Mind brain; the Matrix bot uses the
  handed-over device (`MATRIX_DEVICE_ID`) instead of minting another; Windy
  Mail sends authenticate with the agent's passport token.
- **`windy start --channel matrix`** runs one chat channel in the foreground
  (how a body brought home answers in Windy Chat; plain `windy start` runs
  the voice bridge + gateway). The runtime now loads the project `.env`
  explicitly: a bare `load_dotenv()` searched from the installed module's
  folder, so a pip install never read the agent's own settings.

## 0.7.2.1

Found by the 0.7.2 clean-machine proof from PyPI:

- **`windy deregister` works on a pip install.** It now loads the agent's own
  env file (WINDY_ENV_FILE, else the project `.env`), like `windy ept refresh`,
  so it finds the agent's passport without `--passport`, and after revoking it
  comments the dead token out of `.env` (with a backup) as intended.
- **`windy go` no longer shows a revoked passport as active.** It checks for the
  `# REVOKED` mark `windy deregister` leaves, then asks Eternitas's public
  registry (one 4-second call, fails open), and says plainly that the passport
  is revoked or suspended and that `windy go --force` hatches a new identity,
  instead of "✓ already has a passport" and "✓ Free Windy Mind brain connected".
  A **suspended** passport is a reversible lock: `windy go` says the agent keeps
  its identity until the suspension is lifted and never suggests `--force`.
- `.env` is written owner-only (0600): it holds the passport token.
- `windy login` flushes the sign-in link, so it shows up when output is piped
  (containers, CI).
- The post-hatch screens stop asking a customer for `SYNAPSE_REGISTRATION_SECRET`,
  stop printing a gateway log path when no gateway runs, and no longer tell the
  user to "click the link" when there's no link.

- **Security: a remote hatch can no longer take over the host agent's
  identity** (hatch hallway audit §2e #5). `POST /hatch/remote` now refuses
  an empty `passport_number` with a 400. The hatch subprocess no longer
  inherits the gateway host's `ETERNITAS_PASSPORT`, `ETERNITAS_PASSPORT_TOKEN`,
  `ETERNITAS_OPERATOR_JWT`, `WINDY_HUB_JWT`, `WINDY_ENV_FILE` or
  `WINDY_CREDENTIALS_FILE`. `windyfly.hatch_remote` drops those itself too,
  and no longer defaults `--passport-number` from the environment. Before
  this, an empty passport made the new agent adopt the host's passport.

## 0.7.2

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
