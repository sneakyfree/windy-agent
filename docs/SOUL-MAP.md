# SOUL MAP — every file that makes this agent *this* agent

**Why this document exists.** Guiding Principle #7 promises the Windy agent never forgets: it survives context resets, reinstalls, and machine moves, and picks up mid-thought. That promise is only as good as knowing **exactly which bytes carry the agent's identity and memory** — and until now that knowledge lived scattered across the code. This is the single map. If you are backing up, migrating, cloning, or resurrecting an agent, everything that matters is listed here.

The rule of thumb: **`SOUL.md` + `config.yaml` + `data/` is the agent.** Copy those to a new machine, install `windyfly`, and the same agent wakes up. Everything else in this repo is replaceable machinery.

---

## Tier 1 — the soul (identity; irreplaceable, human-meaningful)

| Path | What it is | Lost if deleted |
|---|---|---|
| `SOUL.md` (repo/project root; path configurable via `personality.soul_path`) | The persona itself — who the agent is, in prose a human can read and edit. Loaded by `personality/engine.py::load_soul()` | The agent's character. It would still function, but it would not be *them* |
| `config.yaml` | Agent config: model defaults, personality preset, budgets, channels, ecosystem wiring (see `config.py::DEFAULT_CONFIG` for the full shape) | Preferences and wiring; recoverable but tedious |

## Tier 2 — the memory (`data/`, path from `platform.py::get_data_dir()` = `<project_root>/data`)

| Path | What it is | Lost if deleted |
|---|---|---|
| `data/windyfly.db` | **The memory itself.** SQLite, ~25 tables: `episodes` (lived experience), `nodes`/`edges` (semantic graph), `goals`, `intents`, `reminders`, `journal`, `agent_actions`, `failures`, `collaborators`, `cost_ledger`, `eternitas_registry`, `analytics_events`, `schema_version` | **Everything the agent remembers.** This is the file that must never be lost |
| `data/matrix_store/` | Matrix/chat client state + encryption keys | Chat identity continuity; re-pairing required |
| `data/offline_queue.json` | Work queued while offline | In-flight actions |
| `data/provision_recovery.json` | Half-finished hatch/provisioning state for resume | Ability to resume an interrupted hatch |
| `data/providers.json` | Model-provider selection state | Routing preferences |

## Tier 3 — credentials living in `data/` (secret; back up encrypted or re-auth)

`data/.anthropic_oauth.json`, `data/gmail_token.json`, `data/google_oauth_creds.json`, `data/google_calendar_creds.json`, `data/google_calendar_token.json`.

> ⚠️ These are live credentials. Back them up **encrypted**, never to a public repo, and prefer re-authenticating on a new machine over copying them. Everything else in this map is safe to copy in the clear.

## Tier 4 — runtime scratch (safe to delete; regenerated)

`data/windyfly.log`, `data/windyfly.pid`, `data/windyfly.lock`, `data/.update_check`, `data/sounds/`, resurrection/grace marker files written by `agent/resurrect.py` (`auto_resurrect` markers, recovery probe, post-recovery grace).

Deleting Tier 4 while the agent is stopped is harmless — it is the difference between "the agent is thinking right now" and "the agent exists."

---

## Continuity operations

**Back up an agent** (stop it first so SQLite is quiescent):
```bash
windy stop
tar czf windyfly-soul-$(date +%Y%m%d).tgz SOUL.md config.yaml data/
```

**Move an agent to a new machine:**
```bash
pip install windyfly            # or the Docker path in DEPLOY.md
tar xzf windyfly-soul-*.tgz     # into the new project root
windy doctor                    # verifies layout + wiring before first start
windy start
```

**Verify continuity is actually working:** the agent scores itself. `agent/maintenance.py` computes a weekly **continuity score** (Principle #7 expressed as a number), surfaced through the Telegram and Matrix channels. A falling score is the early warning that memory or soul persistence is degrading — investigate before it becomes amnesia.

**Resurrection** (`agent/resurrect.py`) intentionally treats its marker files as best-effort: a missing or corrupt marker degrades to "not currently resurrecting" rather than crashing. That is deliberate — the agent must always be able to boot. Real memory lives in Tier 1–2, never in markers.

---

## Related

- Soul *import* from other agent systems (OpenClaw, Hermes) — `src/windyfly/soul_import/`, which reads their `SOUL.md`, `MEMORY.md`, and skills.
- Architecture overview — `docs/ARCHITECTURE.md`.
- Install/operate/recover — `DEPLOY.md`.

**Maintenance policy:** when you add a file under `data/` that carries state, add it to the right tier here in the same PR. A file that isn't in this map is a file nobody will know to back up.
