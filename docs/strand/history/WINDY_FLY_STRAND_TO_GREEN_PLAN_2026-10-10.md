# Windy Fly: DNA strand to green (APPROVED by Grant 2026-10-10 ~23:55Z / 7:55 PM ET)
_Owner: Windy Agent lane. Source audit: https://claude.ai/artifact/W1e8TTtmLinV3Jo5Jq7iYB (542 rows vs master 500aa13: 333 green / 147 yellow / 42 red / 20 retired; checklist 101 codons = 53/32/12/4)._

**Goal:** the strand and the code agree. Every gap closes by building it OR by retiring it on the record; then every codon is an executable CI test so the scorecard cannot go stale. Order = doctrine #10: stability/truth > the owner's control > simplicity > capability.

**Rules unchanged:** one PR per gene; Hub OK on every behaviour PR (exact head); ONE merge waiter; behaviour that touches Zero behind flags where sensible; synthetic proofs on zero-lab only, never Grant's agents/accounts; Zero deploys only on Boss GO (+ Talk OK for the bridge); lean compute.

## Wave 0: RETIREMENTS (Grant APPROVED 10-10)
1. **Mission Control (Phase 8)**: gateway machines.ts + /api/machines* + /ws/terminal + sync-providers + exec routes + legacy UI bits. Reason: no remote daemon exists; exec and key sync over ws://.
2. **Shape-shift (Phase 6)**: agent/shape_shift.py, its tools, gateway /api/shape-shift*, UDS methods, the shape_shift_bias slider (all presets). Reason: unregistered by #490; leaned on retired sub-agents; principle 4.
3. **Executable skills**: skills are text playbooks only. Remove sandbox execution for skills, the 3-gate evaluator, golden tests, the regression suite, and their UDS/gateway routes; promote no longer pretends to gate. Reason: unsafe sandbox; chat already uses playbooks only.
4. **/mode** (companion/focused/neutral): the command, mode API (GET/PUT), get_mode_override, personality/mode.py. Reason: dead; tone overlays are post-it notes (principle 5).
5. **sqlite-vec, never-on Matrix E2E claims, stale @windyfly bot user**: drop the dependency + README claim; remove the false "E2E enabled" log and the dead olm paths (E2E = a Chat-wide decision); fix the toml/branding bot user.
Also already-dead code tidied in the same spirit: agent/sub_agents.py (retired by #490).

## Wave 1: no false "done" (1 PR)
/preset tells the truth while raw_mode is on; /presets descriptions match real values; the false E2E log line; shape_shift_restore and /mode go with Wave 0.

## Wave 2: the owner's data and control (3 PRs, each starts from a failing test)
- 2a Conflicts: hold the new value until the owner chooses; ask in chat (old vs new); "keep old" keeps it.
- 2b Drift: real baseline (older snapshot), ignore owner-made changes, surface on the dashboard with rollback.
- 2c Intents: fix the .format KeyError (LLM inference runs), schedule decay_intents, fix the emotion-trend window (user turns only).

## Wave 3: one of each (2-3 PRs)
- One providers store owned by the brain; gateway reads/writes via the UDS bridge; keys only in a 0600 file; tests for discovery / multi-key / validate / OpenRouter PKCE (+ state param). Mind stays the only path for passport agents.
- Windy Pro tools register only when configured (10-10 rule).
- Event log: emit conflict.detect, intent.surface, skill events that remain; drop types duplicating the cost ledger.

## Wave 4: prove the yellows (tests + small wiring)
In-session recall, CLI chat turn, write-queue order, typing/presence, rollback restores values, journal counter, gateway WS round trip, soul import owner entry point with sensitive-item review.

## Wave 5: strand v2 + executable scorecard
Rewrite the strand (retired codons marked with decisions; new genes: Windy Mind, Eternitas passports, teams/pair rooms, Mail, Calendar, Cloud, phone tools, Vault). Every codon = a CI test under tests/strand/ named for it.

**Size:** ~8-10 working days of the lane.
