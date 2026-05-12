# Stock Toolkit — What Every Windy Agent Is Born With

**Status:** Proposal v1. Surfaced 2026-05-12 when Grant asked Windy 0 to SSH
to Veron-1 and the bot honestly admitted "I don't have an SSH tool." That's
the kind of gap a grandma-tour-ready agent cannot have. This doc fixes the
framework so we never have to discover gaps that way again.

**Audience:** anyone shipping new Windy instances (Windy 0, Windy 1, ...),
anyone forking via HiFly, anyone evaluating "does this product feel finished."

---

## The problem we're solving

A new user — a grandma, a normie professional, a curious developer — should
**never** have to install or configure a tool to get basic agent functionality.
The agent comes alive on first hatch with a sensible default toolkit covering
the daily verbs of digital life.

Today's gap (motivating example): Windy 0 can write to GitHub, send email,
search the web, set reminders, and chat — but cannot SSH. The fleet is mesh-
linked over SSH (Windy 0 is one of 11 machines), the standing CLAUDE.md tells
Kit to "just SSH and check," and the bot has to redirect the user to a sister
agent. That's a UX cliff.

**Goal:** every shipped Windy agent has the Tier 0 toolkit below from the
first boot, configurable but never absent.

---

## The three tiers

Tiers slot into the existing capability model
(`src/windyfly/agent/capabilities/descriptor.py`). The tier names below are
new; the underlying `Band`, `SandboxTier`, and `Tier` enums already exist
and need no changes.

### Tier 0 — Born With (no setup, no API key, no wizard)

Ships in every instance. Either uses local resources (filesystem, subprocess,
existing system binaries like `ssh`, `git`) or hits a no-key public API
(Open-Meteo for weather, Wikipedia for lookup). User experiences these as
"of course the agent can do that."

The promise: on first hatch, the agent can answer **all** of these without
the user touching config:

- "What's 17% of $4,800?" — math evaluator
- "What time is 3pm PST in EDT?" — clock/timezone
- "What's the weather in Phoenix?" — Open-Meteo (no key)
- "Read me this PDF" — local format extractor
- "Describe this image" — local vision (Tesseract OCR + LLM vision)
- "SSH to Veron-1 and check uptime" — host-managed `ssh` binary
- "List files in my Downloads folder" — filesystem
- "Run `npm test` here" — shell.exec (Docker-sandboxed by default)
- "Commit and push this change" — git
- "Fetch this URL and tell me what it says" — http fetch

### Tier 1 — One-Click Add (needs API key, but installable via slash command)

Off by default; the bot can install itself when the user asks. Pattern:
`/add weather` → bot walks the user through getting the key in plain
English ("I need a free key from openweathermap.org — I'll show you exactly
where to click").

Tier 1 lives in the same `ToolRegistry` as Tier 0; the registration is
gated by "has key in env / config." On install, the slash command stores
the key in the soul repo's secrets file and re-registers the tool live.

Tier 1 candidates (most have free tiers):

- Email (read+search, IMAP) — send is Tier 0 via local mail.py
- Calendar (Google Calendar OAuth)
- Maps + directions (Google Maps or OSM)
- Translation (DeepL free / Google Translate)
- SMS + phone calls (Twilio)
- Slack/Discord webhooks
- Stripe (read-only — payment authorization is Tier 2)

### Tier 2 — Power User OAuth (explicit consent flow, off by default)

Anything that touches financial data, the user's contacts/photos, or
operates on shared infrastructure. Requires the OWNER band and an explicit
consent confirmation per session.

- Plaid (bank balance, transactions)
- Stripe writes (payments, refunds)
- Google Contacts / Photos / Drive
- IoT / smart home (Home Assistant)
- Browser automation (Playwright — can fill forms, click things)

---

## Mapping to existing Band / Tier / SandboxTier

The capability descriptor (`descriptor.py`) already has every dimension we
need. The Stock Toolkit tier is **about install friction**, not runtime
risk. A Tier 0 tool can still be band-restricted at runtime.

Example: `ssh.exec` ships as Tier 0 (no install, no key), but its runtime
band depends on the target host:

| Target host                    | Band required | Reason                            |
|--------------------------------|---------------|-----------------------------------|
| In `ssh_allowed_hosts` config  | USER          | Pre-approved by instance owner    |
| Anywhere else                  | TRUSTED       | Treat unknown hosts as risky      |
| Host == `localhost`/`127.*`    | OWNER         | Loopback is shell.exec in disguise|

The Stock Toolkit tier and the runtime Band are orthogonal. Tier answers
"do I have to install it?"; Band answers "am I allowed to run it right now?".

---

## Tier 0 inventory — what we have vs. what's missing

Auditing the current codebase (`src/windyfly/tools/` + `src/windyfly/agent/capabilities/`):

### Have (Tier 0, working)

| Capability/Tool         | Where                                     | Notes                                |
|-------------------------|-------------------------------------------|--------------------------------------|
| `shell.exec`            | `capabilities/shell.py`                   | Docker-sandboxed, full Band model    |
| `fs.*` (read/write/grep/glob/move/delete) | `capabilities/filesystem.py`    | Has undo journal                    |
| `github.*` (writes)     | `capabilities/github.py`                  | PRs, commits, files                  |
| `weather` (Open-Meteo)  | `tools/weather.py`                        | No key                               |
| `web_search` (native)   | `tools/native_web_search.py` (PR #164)    | Anthropic Tier 0 native              |
| `mail.send` (local SMTP)| `tools/mail.py`                           |                                      |
| `sms`                   | `tools/sms.py`                            | Twilio — actually Tier 1            |
| `voice` (TTS/STT)       | `tools/voice.py`                          |                                      |
| `reminders`, `todos`    | `tools/reminders.py`, `tools/todos.py`    | Local DB                             |
| `news`                  | `tools/news.py`                           |                                      |
| `utilities` (timer, translate, convert, random) | `tools/utilities.py`     |                                      |
| `cloud` (Cloudflare)    | `tools/cloud.py`                          | Tier 2 in practice (needs token)     |

### Missing (Tier 0 gaps, ship in priority order)

| Capability                    | Priority | Why it's Tier 0                                |
|-------------------------------|----------|------------------------------------------------|
| **`ssh.exec`**                | **P0**   | Fleet mesh is over SSH; user just hit this gap |
| `http.fetch`                  | P1       | Raw URL fetch as peer to web_search            |
| `vision.describe` / `vision.ocr` | P1   | Local Tesseract + LLM vision; "read this screenshot" |
| `audio.transcribe`            | P2       | Whisper local model; voice notes               |
| `format.pdf_read`             | P2       | Extract text from PDFs                         |
| `format.docx_read` / `xlsx_read` | P2    | Office formats                                 |
| `clock.now` / `clock.parse`   | P3       | Partial in `utilities.py`; needs first-class   |
| `calc.eval`                   | P3       | Don't make Sonnet do arithmetic                |
| `git.read` (log/diff/status)  | P3       | github.py covers writes; need local-repo reads |

### Reclassify (currently mixed, should be Tier 1)

| Tool        | Current    | Should be                                    |
|-------------|------------|----------------------------------------------|
| `sms`       | Tier 0     | Tier 1 — needs Twilio key                    |
| `cloud`     | Tier 0     | Tier 1 — needs Cloudflare token              |
| `news`      | Tier 0     | Tier 1 if key-gated; Tier 0 if RSS-only      |
| `calendar`  | unclear    | Tier 1 — needs Google OAuth                  |

These work fine for instance owners who configure them, but they shouldn't
be in the "born with" promise. Document the install path so the wizard can
target them.

---

## The install wizard (Tier 1 pattern)

Slash commands handle Tier 1 installs. Example flow:

```
User: /add weather
Bot:  Weather is already installed (Tier 0, no setup needed). Ask me about it!

User: /add maps
Bot:  Maps needs a free Google Maps API key. Want me to walk you through it?
      → Yes
Bot:  Three steps. Open this in another tab:
      https://console.cloud.google.com/google/maps-apis/credentials
      1. Click "Create credentials" → "API key"
      2. Copy the key
      3. Paste it back here and I'll save it.

User: AIzaSy...
Bot:  Saved to your soul's secrets file. Testing… ✓ working. Maps installed.
      Try asking me "how do I get to the nearest pharmacy?"
```

This pattern requires:

1. A slash-command handler (`channels/slash_commands.py` already exists)
2. A per-tool install spec — what key, where to get it, how to test
3. A secrets-storage path in the soul repo (NOT the codebase per the
   2026-04-21 architectural rule)
4. Live re-registration after install (no restart needed)

Spec format proposal (per-tool, in the tool file):

```python
INSTALL_SPEC = {
    "key_env": "OPENWEATHER_API_KEY",
    "key_url": "https://openweathermap.org/api",
    "key_instructions": [
        "Sign up (free, no card needed).",
        "Go to API keys, copy the default key.",
        "Paste it here.",
    ],
    "test_call": lambda: get_weather("New York"),
}
```

(This is a v1 sketch. Concrete shape lands when the wizard PR ships.)

---

## HiFly fork compatibility

Per `~/kit-army-config/docs/hifly-fork-strategy.md`, HiFly is the OSS fork
of windy-agent. The Tier 0 toolkit is the **public surface** of that fork:
anyone running HiFly should get the same born-with experience.

Implications:

- Tier 0 tools must work **without** the soul repo's secrets (no instance-
  specific data baked in)
- Tier 1 install instructions ship in the OSS fork (they reference public
  APIs, not internal Windy systems)
- Tier 2 power-user tools may be Windy-specific (Plaid integration tied to
  Stripe account, etc.) — those don't have to be in HiFly

This means **the Tier 0 PRs are HiFly contributions in advance**. Each
Tier 0 PR closes a fork gap.

---

## PR sequencing (proposed)

One PR per tool, smallest viable scope. All follow the established pattern
(file in `tools/` or `capabilities/`, registration in `boot.py`, tests in
`tests/`):

1. **PR #168 (next):** `ssh.exec` capability — the immediate gap
2. **PR #169:** `http.fetch` tool — raw URL fetch with optional JS rendering
3. **PR #170:** `vision.describe` + `vision.ocr` — Tesseract local + Anthropic vision
4. **PR #171:** `audio.transcribe` — Whisper local
5. **PR #172:** `format.pdf_read` + `format.docx_read` — text extraction
6. **PR #173:** `clock.*` first-class + `calc.eval` — promote from utilities
7. **PR #174:** `git.read` — local repo log/diff/status (peer to github.py)
8. **PR #175:** Tier 1 install wizard — `/add <tool>` slash command + spec format
9. **PR #176 onward:** Tier 1 fillout (calendar, maps, translation, ...)

Each PR is sized like the recent #160-#167: surgical, tested, self-mergeable
under the 2026-04-26 standing authority.

---

## Anti-goals (what this doc is NOT)

- **Not** a rewrite of the capability system. The Band/Tier/SandboxTier
  model already exists and works. We're documenting it + filling gaps.
- **Not** a security review. Each tool PR carries its own audit story
  (timeouts, blocklists, sandboxing). This doc names the contract; the
  PRs deliver the safety.
- **Not** a marketing pitch. No mention of "the AI that can do anything."
  Tier 0 is a humble list of common-sense defaults.
- **Not** locked in forever. If a Tier 0 tool turns out to need a key in
  practice, demote it to Tier 1 and update the wizard.

---

## Open questions

1. **Where does the install wizard store keys?** Proposal: in the soul
   repo (e.g., `~/windy-0-soul/secrets/tier1.toml`), not in the windy-agent
   repo. Soul repos already host instance-specific config per the
   2026-04-21 architectural rule. Confirm before PR #175.
2. **Tesseract as a hard dependency?** Vision OCR needs Tesseract installed
   at the OS level. Either ship a fallback (LLM-vision-only mode) or
   require Tesseract at hatch. Lean toward fallback.
3. **Should `ssh.exec` be a Capability (band-gated like shell) or a Tool
   (LLM-facing like weather)?** Lean Capability — it's a "do real things"
   action, same risk class as shell.exec.
4. **`shell.exec` is currently Docker-default; should `ssh.exec` follow
   the same pattern (route through a sandboxed jumphost)?** Probably not
   for v1 — the user's own SSH config is already a trust boundary. But
   worth a security review before PR #168 lands.

---

## Decision log (this doc)

- 2026-05-12 — Drafted in response to Grant's "Windy 0 can't SSH?!" finding.
  Establishes the Tier 0/1/2 framework, audits the current toolkit, and
  proposes a PR sequence starting with `ssh.exec`.
