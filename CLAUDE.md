# Windy Agent (Windy Fly) — AI Context File

This file is automatically loaded by Claude Code / AntiGravity at conversation start.
It contains critical project knowledge that prevents regressions.

## ⚠️ ECOSYSTEM CONTEXT (READ FIRST)

This repo (`windy-agent`) is the **developer name** for what consumers see as **Windy Fly** — the AI agent product of the Windy ecosystem (ReAct loop, memory, multi-channel adapters). It is one of 13 canonical Windy platforms plus Eternitas + the Authenticator + various infrastructure pieces. **HiFly** (deferred OSS fork) forks from this repo per `FORK.md` + `~/kit-army-config/docs/hifly-fork-strategy.md`.

**Before working on this repo, load the ecosystem context:**

1. **`~/kit-army-config/docs/adr-010-vision-aligned-engineering-invariants-2026-05-08.md`** — the canonical alignment doc. 13 platforms permanent, dual-shell coexistence, mobile-first, voice-as-API, BYOM via Windy Mind, no-stopwatch ethos. **READ THIS FIRST.**
2. **`~/kit-army-config/docs/adr-011-eternitas-universal-agent-identity-registry.md`** — Eternitas is an independent Utah LLC; passport issuance happens at hatch.
3. **`~/kit-army-config/docs/adr-012-windy-mobile-mvno-os-hardware.md`** — long-term Windy Mobile vision (deferred until ecosystem maturity).
4. **`~/kit-army-config/docs/windy-search-bot-traffic-monetization.md`** — proto-Google-for-agents thesis; agent traffic is the strategic moat this product feeds.
5. **`~/kit-army-config/docs/hifly-fork-strategy.md`** — HiFly is the OSS fork of windy-agent (deferred); keep the codebase fork-friendly.
6. **`~/kit-army-config/ACCESS_LOCKBOX.md`** — credentials lockbox (private repo). Source of truth for all secrets, AWS keys, API tokens, deploy commands.
7. **`~/.claude/projects/-Users-thewindstorm/memory/MEMORY.md`** — auto-loaded persistent memory. Index of all locked decisions across the ecosystem.

**Dev-name ↔ consumer-brand mapping (don't conflate):**
- `sneakyfree/windy-agent` = "Windy Fly" (this agent product)
- `sneakyfree/windy-pro` = "Windy Word" (the hub product / account-server)
- All other repos: 1:1 dev-name ↔ brand (windy-mail ↔ "Windy Mail" etc.)

**Sister repos most relevant to this one:**
- `windy-pro` — account-server / identity hub. Source of human JWTs; agents redeem EPTs against its JWKS.
- `windy-chat` — Matrix-based comms hub. Auto-provisioned at hatch so every agent has a chat handle.
- `eternitas` + `eternitas-authenticator` — passport issuer. Every agent gets an EPT at hatch.
- `windy-mind` — multi-model intelligence layer (BYOM); the agent loop talks to model providers through Mind.
- `windy-search` — agent web-access toolkit (Platform 13); the agent's web-traffic surface.

When making cross-product engineering calls, default to **kit-army-config docs as canonical**. Repo-specific notes (branching policy, exception log, architectural rules) follow below.

---

# CLAUDE.md

Project-scoped instructions for Claude Code agents working in
`windy-agent`.

## Branching Policy

This repo follows Grant's ecosystem-wide Branching Policy: feature
branches + PR review, **no direct pushes to `master`**. See
`~/.claude/projects/-Users-thewindstorm/memory/feedback_branching_policy.md`.

### Exception log

Exceptions to the Branching Policy are always narrow and one-time.
Every exception must be logged here with a reason and a scope.

- **2026-04-17 — `wave-7-batch-only`.** Bucket A of
  `docs/MERGE_TRIAGE.md` (PRs #2, #11, #14) is authorized to self-merge
  as part of the Wave 7 batch-merge pass. Scope: the three Bucket A
  PRs only, for this batch only, squash-and-delete-branch. Does NOT
  extend to Buckets B–E. Does NOT extend to future waves or future
  self-merges. Buckets B merges require manual smoke; Bucket C stops
  for review; Bucket D surfaces decisions; Bucket E defers.
- **2026-04-17 — doc-only direct commits to master.** Triage and
  decision documents (`docs/MERGE_TRIAGE.md`, `docs/BUCKET_C_REVIEW_REQUEST.md`,
  `docs/BUCKET_D_DECISIONS.md`) are authorized to commit direct
  to `master` without a PR. Scope: these three files, for this
  batch only. Code changes remain PR-only.
- **2026-04-21 — Wave 15 #0 instance-config split (direct-to-master).**
  Removal of `windy-0.toml` and `scripts/run-windy-0.sh` from this repo
  is authorized as a direct-to-master commit. Reason: instance-specific
  files were leaking into the generic codebase; relocating them to
  `~/windy-0-soul/` honors the architectural model (windy-agent =
  generic class; per-instance soul repo = config + launcher + identity).
  Scope: the deletion only. New copies live in the soul repo. Bot
  verified booting clean on the new launcher path before commit.
- **2026-04-26 — Kit Zero acting as autonomous maintainer (standing
  authority).** Grant explicitly delegated technical decision-making
  to Kit Zero on 2026-04-26: "I have no ability whatsoever to review
  branches or merge branches or anything like that." Without
  delegation the PR queue is dead-end work — fixes never reach
  production. Standing authority granted to Kit Zero:
  1. Self-merge own PRs after self-review (diff sanity check + full
     local test suite green) using `gh pr merge --squash --admin`.
  2. Bypass pre-existing CI failures only when the failure is style-
     only (ruff F-class warnings, mypy strict-mode noise) and not a
     functional regression introduced by the PR. Functional test
     failures still block.
  3. Direct-commit to master for: lint-debt cleanup, code comments,
     CLAUDE.md exception log entries, and any change <20 lines that
     doesn't touch agent loop / capability handlers / channel
     adapters.
  4. Substantial features (>100 lines, new capabilities, channel
     adapters, schema migrations, security boundaries) still go
     through a feature branch + PR + tests for traceability — Kit
     Zero merges after self-review, but the PR exists as a record.
  5. Roll back own merges (`git revert`) freely if production
     observation reveals a regression.
  Reason: Grant is the visionary, not a maintainer. Self-merge with
  test-green and self-review preserves the discipline of the
  branching policy (commit message, PR description, change record)
  while removing a bottleneck that was blocking production fixes.
  Scope: standing — applies to all sneakyfree/windy-* repos and to
  sneakyfree/anima. Revisit if Grant adds another developer to the
  team.

## Architectural rule (post-Wave 15 #0)

This repo must contain **no instance-specific files** (no `<name>.toml`,
no `scripts/run-<name>.sh`, no per-instance hardcodes). Each Windy Fly
instance keeps its config + launcher in its own soul repo (e.g.,
`sneakyfree/windy-0-soul`). The agent codebase here is the generic
"class"; soul repos are the "instances." Future instances should clone
this repo untouched and clone their own `<name>-soul` repo for everything
specific to them.

## Windy Admin telemetry (stress-test contract) — 2026-07-07

**There is ONE super-admin dashboard for the whole ecosystem at
`https://admin.windyword.ai`** (repo `sneakyfree/windy-admin`, ADR-WA-001).
Every platform PUSHES small content-free JSON events to its central ingest;
the dashboard reads them (Fleet / Members / Agents / Cost / Experiment /
Audit). **When you stress-test this platform, it is part of the job to prove
its activity shows up on that dashboard.** Missing telemetry is a GAP to
close, exactly like a bug — and each platform verifying its own telemetry in
parallel doubles as a load test of the panel.

**This repo:** LIVE — the fly emits one llm.call per completed turn. Code: `src/windyfly/observability/admin_telemetry.py` + the turn-completion block in `agent/loop.py`. Env in `~/.windy/windy-0.env` on Windy 0. Token `…__WINDY_AGENT`.

**Verify during your stress test** (read token `verify-oc5` in lockbox
`secrets/windy-admin/ingest-tokens.env`):
```bash
curl -s "https://admin.windyword.ai/v1/events/tail?limit=10&platform=windy-agent" \
  -H "Authorization: Bearer <verify-oc5>" | python3 -m json.tool
```
Drive real traffic, then confirm your events appear here AND on the dashboard
page they feed. If they don't, fix the emitter (or the aggregation query).

**Hard rules (non-negotiable):**
- **Fire-and-forget**: post async, short timeout, swallow every error. A dead
  ingest must NEVER break this product (proven: chat runs fine with the ingest
  down).
- **Inert unless configured**: no-op when `WINDY_ADMIN_INGEST_URL` /
  `WINDY_ADMIN_INGEST_TOKEN` are unset.
- **Privacy hard line**: counts / costs / durations / models / ids only. Cost
  is INTEGER microcents (10^-6 USD). The ingest 422s any metadata key whose
  camelCase/snake tokens match content/text/body/message/prompt/transcript/
  subject/html/completion/reply — if you get 422'd, FIX THE EVENT, never ask
  for the guard to be loosened.

**Full brief + per-platform table + how-to-instrument:**
`~/kit-army-config/docs/windy-admin-telemetry-campaign-2026-07-07.md`.

## CI: self-hosted runner (since 2026-07)
GitHub Actions runs on OUR runner (kit0-windy-agent on the Kit 0 VPS), not GitHub's cloud.
Always `runs-on: [self-hosted, linux, x64]` — NEVER `ubuntu-latest` (billing-locked; runner-lint enforces).
Jobs stuck "Queued" = runner down, not billing: ssh Kit 0 → cd /home/github-runner/runners/windy-agent && sudo ./svc.sh status
Full runbook: ~/kit-army-config/docs/ci-runner-runbook.md
