# Wave 7 merge triage — Windy Fly

Triage of the 17 open `wave-7-*` / `wave-2-6-*` PRs on
`github.com/sneakyfree/windy-agent`. Generated for the batch-merge
pass on 2026-04-17.

Full rehearsal-verified merge order and the three
manual-resolution hotspots live on PR #1 as comments:
- Playbook: `https://github.com/sneakyfree/windy-agent/pull/1#issuecomment-4271157878`
- Rehearsal findings: `https://github.com/sneakyfree/windy-agent/pull/1#issuecomment-4271390865`

---

## Bucket A — MERGE NOW (3 PRs)

Pure docs or pure test additions. Zero product-code change. Land
these first as warm-up; they clear review backlog without risking
behaviour.

- **#2** — GAP ANALYSIS (28 issues) — 657-line doc-only PR (GAP_ANALYSIS.md + 3 audit files under `docs/audit/`). Read-only artefact; no code surface.
- **#11** — Fix P1-O1: correct observability claims in deploy doc — deploy-doc corrections + new `deploy/aws/logrotate.conf`. No Python/TypeScript touched.
- **#14** — Fix P1-O2 + P1-O3: coverage for vps_deploy and ecosystem_health — 30 new tests (425 add/0 del), no product code changed; covers two modules that were at 0% coverage.

## Bucket B — SAFE WITH SMOKE (3 PRs)

Real fixes with user-visible effects but well-tested, narrow
blast radius, and reversible. Worth one manual smoke per PR before
merge (curl the relevant endpoint / run the relevant CLI command).

- **#4** — Fix P0-S4: SSRF-safe fetcher for /web — 36 contract tests; adds `safe_fetch` module + wires `cmd_web`. Smoke: `/web http://169.254.169.254/…` returns "Refused" on a VPS-like environment; a public URL still fetches.
- **#10** — Fix P1-O4: concurrent Database() migration race — pure defensive; reorders PRAGMA + wraps migrations in `BEGIN EXCLUSIVE`. 5 new tests + 20/20 solo / 3/3 full-suite stability. Smoke: `pytest tests/test_stress_ecosystem.py` shouldn't flake.
- **#15** — P2 polish batch: TrustPanel loading, README counts, .env.example drift — frontend loading-state for Identity page, README test counts, 21 new `.env.example` entries. Smoke: hit the Identity dashboard before the brain finishes booting; confirm the new "Loading trust state…" placeholder renders.

## Bucket C — HIGH RISK — NEEDS EYES (9 PRs)

Auth / crypto / identity / schema / webhooks territory. Each needs
a human reviewer skim + the smoke recipe in the playbook before
merge. Most are stacked around `gateway/src/server.ts` — merge
them in the rehearsal-verified order so conflict resolutions are
mechanical.

- **#1** — Waves 2-6 prerequisite — 3,772-line base rollup containing Wave 2 (ecosystem hatch) + Wave 3 (bot-key + trust gate) + Wave 4 (live Eternitas) + Wave 5 (live integration suite) + Wave 6 (prod prep). Every other PR targets this as base; **must merge first**.
- **#3** — Fix P0-S3: gate slash commands against remote RCE — command registry's channel policy + trust gate + dropped `shell=True`. Changes command behaviour on every channel. 12 new tests.
- **#5** — Fix P0-S1: check peer socket IP, not the Host header — `isLocalhostRequest` signature change threads `server` through. 20 bun tests. Stale Python test in `test_gateway_hardening.py` needs follow-up patch (in playbook).
- **#6** — Fix P0-S2: kill ?auth= query-param password leak — POST login + cookie rewrite. 7 bun tests. Conflicts with #8; resolve as a pair per playbook.
- **#7** — Fix P0-T1+T2: wire trust.changed webhook + verify both signatures — new gateway route, new Python bridge handler, new `trust/verify.py` (HMAC + ES256 JWS verifier). 17 new tests. New dep: `cryptography>=42`.
- **#8** — Fix P1-S5+S6+O5-auth — fail-closed startup guard + constant-time compare + auth rate-limit bucket. 11 bun tests. Supersedes #6 on the auth path; resolve conflict by taking #8's body.
- **#9** — Fix P1-E1+E2: canonicalize ETERNITAS_URL + derive identity from JWT — new `eternitas/url.py` resolver + `auth/jwt_claims.py`. 16 new tests. Modifies 5 existing test fixtures (env scrubbing).
- **#12** — Fix P1-O5 + P2-S7 + P2-S8 — rate-limit buckets + LRU cap + strict CORS. 9 new tests. Conflicts with #8 (rate-limit struct rewritten); resolution requires hand-union of bucket types per playbook.
- **#13** — Fix P1-E3+E4: wk_ key minted before cloud quota — new `_step_mint_bot_key` in hatch orchestrator. 8 new tests.

## Bucket D — BLOCKED ON GRANT (0 PRs)

None. Every open PR is technically self-contained and can merge
without a decision from Grant. Downstream product decisions (e.g.
"which CLI command should replace the CloudWatch stub?", "what's
the production value for `WINDYFLY_ENV`?") don't block the merge —
the fixes leave doors open rather than closing them prematurely.

## Bucket E — DEFER (2 PRs)

P3 polish from the gap analysis. Real fixes but "file and forget"
severity — launch is not blocked. Land in a follow-up wave.

- **#16** — Fix P3-D6: dashboard URL in hatch email is overridable — `WINDYFLY_DASHBOARD_URL` env override. Default URL still points at `windyword.ai/app/fly`, which is correct for public launch; the override is only needed for internal betas.
- **#17** — Fix P3-E5: ecosystem shape canary + Phase 8 doc drift — `_check_health` grows optional `expected_service` param; localhost:7890→3000 doc fixes. The port fix is A-class, but it's bundled with the canary (B-class) so the whole PR sits in E. If the port mismatch hurts, cherry-pick just the doc changes.

---

## TOP 3 MUST-MERGE BEFORE LAUNCH

Ordered by blast radius of the attack each closes.

1. **#3 — Fix P0-S3: gate slash commands against remote RCE.** Without this, anyone who can reach any configured messaging channel (SMS, Matrix, Discord, Telegram, Slack, WhatsApp, Signal, IRC, Teams, Email, CLI) sends `/run <shell>` and gets arbitrary code execution with the agent owner's privileges. Every other P0 is secondary to this one — if RCE ships to a user's machine, nothing else matters. User pain: agent's entire memory DB + cached `wk_` bot key exfiltrated on first hostile message.

2. **#4 — Fix P0-S4: SSRF-safe fetcher for /web.** On a VPS deploy (the paid path), `/web http://169.254.169.254/latest/meta-data/iam/security-credentials/` returns the EC2 instance role credentials. Combined with #3's command-registry bypass, anyone who reaches the agent from any channel can exfiltrate AWS credentials. Also blocks `file://`, `gopher://`, and hostname-based rebinding attacks. User pain: cloud account pwn, not just the agent.

3. **#7 — Fix P0-T1+T2: wire trust.changed webhook + verify both signatures.** The documented `trust.changed` subscription **does not exist** in the current code — `handle_trust_changed` has no HTTP route. When a passport is revoked or suspended by Eternitas, the agent continues honouring the 5-minute TTL cache window as if the revocation hadn't happened. Launch-day credibility issue (the trust story is false without this) AND a live-window exploit: an abusive bot can keep operating for up to 5 minutes after its operator has been caught. This PR also ships the dual-signature verification (HMAC + ES256 JWS) that the webhook receiver needs to resist forged cache flushes, so it closes both P0-T1 and P0-T2 as one unit.

**Precondition for all three:** PR **#1** must merge first — it's the base containing the Wave 2-6 code these three are fixing. No fix PR is reachable from `master` without #1. Treat #1 as the zeroth must-merge; the top 3 are the security-critical fixes on top of that foundation.

---

## Numbers

- **17 PRs total** in the queue, all on `github.com/sneakyfree/windy-agent`.
- Cumulative diff: **~8,100 added / ~200 deleted** across all PRs (net ~+7,900 lines). Dominated by #1 (~3,772 add) and #2 (~657 add), with the rest averaging ~250 lines per PR.
- **180+ new tests** across the fix PRs. Post-merge rehearsal suite: 1,248 pass / 37 skipped / 0 fail.
- **3 conflict hotspots** on `gateway/src/server.ts`, pre-resolved in the playbook on PR #1.

## Merge-day execution order

1. Bucket A (3 PRs, ~5 min each) — doc + tests only, rubber-stamp.
2. #1 — the base — **merge-commit**, not squash.
3. #10 (Bucket B) — clears the pre-existing concurrency flake.
4. Bucket C in rehearsal order: #3 → #4 → #5 → #6 → #8 → #12 → #7 → #9 → #13.
5. Remaining Bucket B (#4 was already above; #15).
6. Bucket E (#16, #17) — last or post-launch.

Total reviewer time: **~45 min**. Each PR has its own squash commit.
