# The terminal door — where it goes and where it diverges

**Written 2026-07-31 for TASK 5 of the hatch-consolidation pack**
(`eternitas/docs/hatch-consolidation/01-windy-agent.md`). Report only —
nothing in the terminal lane was changed in this pass.

Everything below marked **VERIFIED** was established by running it. Everything
marked **READ** was established by reading the code. That distinction has
already produced one wrong conclusion in this investigation, so it is kept
explicit.

---

## First, a correction to the brief

**There is no `windyfly hatch` command.** VERIFIED — `windy --help` lists 38
subcommands and `hatch` is not among them, and `grep -n "hatch" src/windyfly/cli.py`
returns four hits, all of them the *animation* (`play_hatching`) or the status
panel, never provisioning.

The terminal door is **`windy go`**:

```
windy go
  └─ quickstart.py::_try_hatch_provisioning()          # naming ceremony, prompts
       └─ hatch_orchestrator.py::run_hatch()           # sync wrapper
            └─ hatch_orchestrator.py::orchestrate_hatch()   # ◄── THE HALLWAY
```

## It reaches the same hallway — confirmed

**VERIFIED.** `_try_hatch_provisioning` calls `run_hatch(...)`, which is a thin
`asyncio.run` wrapper around `orchestrate_hatch(...)` — the identical
orchestrator the browser and mobile doors reach through
`hatch_remote.py`. There is one orchestration, not two that resemble each
other. Same `_step_eternitas`, same `_step_birth_certificate`, same client
selection through `get_eternitas_client`.

It reaches the same **issuer** too: Eternitas, via `EternitasClient`.

---

## Divergence 1 — a different endpoint on the same issuer 🔴

This is the one that matters, and it is the "two different Eternitas endpoints
in use" the 2026-07-30 census found.

| Door | Starter | Eternitas endpoint | Auth |
|---|---|---|---|
| Browser | windy-pro | `POST /api/v1/bots/auto-hatch` | anonymous (Turnstile) |
| Mobile | windy-pro | `POST /api/v1/bots/auto-hatch` | anonymous (Turnstile) |
| **Terminal** | **windy-agent** | **`POST /api/v1/bots/register`** | **operator `X-API-Key`** |

`00-THE-CONTRACT.md` is explicit that `/bots/auto-hatch` is the one consumer
door — it is the one with the human-verification gate on it — and that
`/bots/register` "remains for programmatic/enterprise registration by
already-verified operators and leaves the consumer ceremony entirely."

The terminal door is a consumer ceremony using the enterprise endpoint.

## Divergence 2 — the terminal door cannot currently mint at all 🔴

**VERIFIED by running**, against a real Eternitas with no operator key set:

```
ERROR Eternitas registration failed:
      Client error '401 Unauthorized' for url '/api/v1/bots/register'
passport_id: ''
certificate: ''
```

`/bots/register` requires an operator whose `verification_status` is `VERIFIED`
(READ — `eternitas/src/eternitas/routes/bots.py:59`). Anonymous callers get 401.

And the key is blank everywhere it is declared:

- `.env.example:94` — `ETERNITAS_OPERATOR_KEY=` (empty)
- `.env.production.example:140` — `ETERNITAS_OPERATOR_KEY=` (empty)
- **Windy 0's live `~/.windy/windy-0.env` — empty/absent** (VERIFIED, read on the box)

So on a fresh laptop, and on the production agent host, `windy go` **cannot
obtain a passport today**. It completes with `passport_id: ''` and a red
Eternitas step.

Windy 0 is unaffected in practice because it already holds an
`ETERNITAS_PASSPORT` from an earlier hatch — and after the pre-allocated-passport
fix (PR #345) a re-hatch there now *adopts* that passport instead of 401ing,
which is strictly better than the previous behaviour.

**Why this matters for August:** if any bootcamp attendee is pointed at the
terminal door, they hit this. The browser door is unaffected.

## Divergence 3 — `windyfly.toml`'s `ecosystem` block is ignored for Eternitas

**READ.** `get_eternitas_client(db, config)` checks `config["ecosystem"]["eternitas_url"]`
*first*, then falls back to the `ETERNITAS_URL` env var. But **neither door
passes `config`**:

- terminal: `run_hatch(agent_name=…, owner_id=…, owner_name=…, db=db)` — no `config`
- browser/mobile: `orchestrate_hatch(agent_name=…, owner_id=…, owner_name=…, on_event=…)` — no `config`

So `windyfly.toml`'s shipped `eternitas_url = "https://api.eternitas.ai"` is
dead configuration for the hatch, and both doors depend entirely on
`ETERNITAS_URL` being in the environment. It is set on Windy 0 (VERIFIED), so
production works — but the config file says something that is not true.

This is consistent across both doors, so it is not a *divergence between* them;
it is listed because it looks like a safety net that is not actually attached.

## Divergence 4 — presentation only (fine, and by design)

The contract says *presentation may differ per door; the record may not.* These
are all presentation and need no action:

- The terminal door passes **no `on_event` callback** — it prints results with
  Rich instead of emitting the `eternitas.*` / `hatch.complete` SSE event
  stream the remote lane produces.
- It runs the interactive **naming ceremony** (`Prompt.ask`) before
  provisioning; the remote lane receives `agent_name` already chosen in the UI.
- It renders the certificate with `render_birth_certificate_terminal`.
- In the offline/dev lane (no `ETERNITAS_URL`) it saves the clearly-labelled
  local **preview** PDF via `save_birth_certificate`. This is a preview, not a
  second issuer — its number comes from Eternitas or reads `(pending)`.

---

## Recommendations (not done in this pass)

1. **Point the terminal door at `/bots/auto-hatch`.** It is the consumer door,
   it needs no operator key, and it is where the human-verification gate lives.
   This closes Divergence 1 and 2 together and is the single change that makes
   "one issuer, one door" literally true. Needs a decision on how Turnstile is
   satisfied from a terminal — likely the same anonymous path, since
   `auto_hatch_require_pro_jwt` and `turnstile_secret_key` are both server-side
   flags.
2. **Pass `config` into `orchestrate_hatch` from both doors**, so
   `windyfly.toml` stops being decorative. Note this flips precedence
   (config would win over env), so it wants its own PR and a check of what
   production actually relies on.
3. Leave the preview renderer alone — it is alive and load-bearing for offline
   development (see PR #347's description).
