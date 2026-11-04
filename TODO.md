# TODO: windy-agent (Windy Fly)

Owner: the Windy Agent lane (Veron). Boss's rule, 2026-11-04:
- Read this file first, every session. Add every promise here in the same turn.
- A line leaves only into Done (shipped and tested) or by a Boss BOARD line.
- Never delete an unmerged branch without its owner's verdict. Tag it `archive/<branch>-<date>` first.
- Dates assume CONTINUOUS work (Grant, via Boss, 11-04). A date more than 5 days out names ONE hard constraint.
- If a PR waits more than 2 h for Hub's read, ask a second qualified reader (Admin, Cloud or Word) and tell Boss.

Last re-dated: 2026-11-04 15:30Z.

## Now

- **Phone-desktop QR pairing, desktop side (FIRST PRIORITY, Boss 11-04).** `windy home pair` already shows the Connect QR link and code. It was merged DARK in #700 (eb80387d).
  - Today 11-04: ask Boss which further parts of the first priority are Fly-desktop's: QR scanner, full-fidelity capture and transfer, the capture crew.
  - Done means: the scope is written here, with a date for each part.
- **Pairing progress gate (pre-flag), one small PR.** Added 11-04.
  - What: show the 428 stage, expires_in and waiting_for; after 60 s with no stage, say "still finishing, this can take a few minutes"; send download and import progress events in JSON mode and person mode; add a test that fails if any long stage goes more than 10 s with no moving number.
  - Old date: before 11-10. New: PR open Thu 11-05, merged Fri 11-06.
  - Done means: merged before Connect's flag on 11-10.
- **Connect pairing lab, legs 1-5.** Thu 11-05, 13:00-14:00Z. Computer A = OC5 lab171d; computer B = Veron.
  - Needs: Boss's GO for the prod flag window, Mobile's lab phone and a TEST owner.
  - Done means: BOARD AFTER with each leg's result, both pair homes shredded, the TEST owner purged.
  - Leg 4's block (agreed with Connect 11-04): ETERNITAS_URL=http://127.0.0.1:9 on that ONE process only. Never a firewall, /etc/hosts or DNS change. Before the lab, a dry run must show the failure lands at key registration. The BOARD BEFORE line names the method.
- **web_search on a key-only paired computer.** Say plainly that search is not available there yet, instead of failing. Added 11-04.
  - Words need Boss and a non-author Hub #9.
  - Old date: before 11-10. New: PR Thu 11-05.
- **Calendar book_appointment key.** Make it deterministic: sha256(passport + starts_at_utc + lower(booker_email)). Don't widen it. Added 11-04.
  - Old date: this week. New: PR Thu 11-05.
- **Vault: release only revocable leases.** Covers call_api.py:218, client.Lease parsing `revocable`, and tools/vault-lab.py. Added 11-03.
  - Old date: none. New: PR Thu 11-05.
- **/model confirmation fix.** Check GET /v1/catalog/me. Boss's line: "I can't switch to <model>: <reason>. I'm still answering with <current model>." Added 11-03.
  - Old date: none. New: PR Thu 11-05.
- **CL5a: Zero owner-call flip.** WINDY_OWNER_CALL=1, restart both, then ONE refused test call. The pin is 633cfc85.
  - Old date: Wed 11-04 15:00Z (no GO came). New: Thu 11-05, 15:00-15:40Z.
  - Constraint: a Kit 0 slot, which needs Boss's GO and Hub's OK.
- **Hub's 7 non-blocking #700 follow-ups.** Added 11-04.
  1. A partway old-door bring-home: tell the person to finish with `windy bring-home`.
  2. Put the memory outcome in the pending marker.
  3. Send ConnectError to line 4.
  4. Ctrl-C handling (at the y/N question, and while registering).
  5. A test for the env-file passport branch.
  6. Show the absolute DB path.
  7. Stop the test suite leaking into the checkout. The leaks: cloud_backup state, the auth audit log, the ept lock, data/windyfly.db.
  - Old date: after #700 (no date). New: one PR, Fri 11-06.
- **Retire latch and 60 s probe.** A refused mint (retired, suspended or revoked) means no model, no local backup, no tools and no outward sends. It clears on the next good mint. Added 11-04 (Boss YES).
  - Measure N for Hub's windy-contracts #265 1.1.0. The person line is Boss's draft, with <date> from `retired_until`.
  - Old date: after the progress gate (no date). New: PR Fri 11-06, with N sent to Hub the same day.
- **Desktop-door installer.** `windy home install --ticket - --no-backup --json-status`, built against Hub's contract draft.
  - Old date: Fri 11-13. New: my side as a PR (DARK) Fri 11-06. End-to-end lab Wed 11-11.
  - Constraint for 11-11: Hub's desktop-door PR, which goes up DARK Tue 11-10.
- **TODO.md in all three repos.** Old date: Fri 11-06 (due Mon 11-09 12:00Z). New: PRs opened today, 11-04: windy-agent #702, windy-fly-site #19, windy-0-soul #1. Done means: merged with Hub's read.
- **My own branch verdicts** (Branches section below). Old date: Fri 11-13. New: my verdicts Fri 11-06. Other owners' verdicts are due by Boss's date, Fri 11-13.

## Waiting on

- **Grant:** his own typed OK in this terminal before I add "Read TODO.md first; add every promise to it the same turn." to CLAUDE.md in windy-agent, windy-fly-site and windy-0-soul. Asked 11-04 11:45Z. Next nudge: my next turn with Grant.
- **Boss:** GO for CL5a in the Thu 11-05 15:00-15:40Z slot, plus Hub's OK. Asked again 11-04 in the dates message. Next nudge: Thu 11-05 12:00Z.
- **Boss:** GO for the Connect lab's prod flag window, Thu 11-05 13:00Z. Connect asked 11-04. Next nudge: Thu 11-05 11:00Z.
- **Boss and Hub #9:** words for the web_search "not available yet" line, and for Calendar's 422 sentence. Next nudge: Thu 11-05.
- **Hub:** the desktop-door contract (Thu 11-05) and the desktop-door PR, DARK (Tue 11-10).
- **Hub:** the "my Windy" GET PR. Next: wire Fly to it the day it merges.
- **Eternitas:** Q9, confirming that the `retired_until` field is in the agent_retired refusal. Next nudge: Fri 11-06.
- **Calendar:** the exact shape of the 422 idempotency_key_reused response, coming with its contract PR.
- **Windy Search:** mode-B live (PR Mon 11-09, live Wed 11-11). Then Fly mode-B for Search: EPT + agent with aud windy-search, plus DPoP, on POST /v1/search and POST /web/fetch. My PR is due Thu 11-12; the constraint is Search's mode-B going live Wed 11-11.
- **Windy Talk:** claim Talk and talk-site by Thu 11-05 18:00Z. If Talk doesn't claim, I write their TODO.md by Mon 11-09 12:00Z.
- **Boss:** which parts of the first priority (full-fidelity capture and transfer, the capture crew) are Fly-desktop's. Asked 11-04 15:33Z in the dates message. Next nudge: Thu 11-05 09:00Z.
- **Boss:** stamp and decisions on #687 (ONE-WINDY-PLAN; Chat's spike proposal and draft roster notice words).

## Later

- **Purge the TRGE lab agent on OC5.** Fri 11-06, at the passport purge. Added 11-04; it waits on the scheduled purge.

## Branches

Unmerged remote branches on 2026-11-04. Verdicts: mine by Fri 11-06, other owners' by Fri 11-13.

| Branch | Last commit | Owner | Proposed verdict |
|---|---|---|---|
| archive/gauntlet/* (12 branches) | 2026-05-21 | Grant | Already archived. Keep. |
| docs/one-windy-plan | 2026-11-04 | Windy Agent (Kit OC1 author) | Open PR #687. Keep. |
| docs/memory-options | 2026-11-02 | Grant | Ask the owner. |
| feat/lean-fly | 2026-11-02 | Kit OC1 | Ask the owner. |
| release-0.7.x | 2026-11-02 | Grant | Release line. Ask the owner. |
| release/v0.7.7 | 2026-10-22 | Kit OC1 | Release line. Ask the owner. |
| release/0.7.6 | 2026-10-04 | Grant | Release line. Ask the owner. |
| wip/windy0-crash-windy-agent-20261020 | 2026-10-20 | Windy Agent | Mine. Check whether its work is on master, then give a verdict by 11-06. |
| wip/windy0-crash-windy-agent-20261020-b | 2026-10-20 | Windy Agent | Mine. Same check. |
| hotfix/zero-db-lock-7311e0e | 2026-10-16 | Grant | Ask the owner. |
| fix/backup-key-required | 2026-10-10 | Grant | Ask the owner. |
| feat/mind-helper-wiring | 2026-09-30 | Grant | Ask the owner. |
| feat/snapshot-key-flagged | 2026-09-30 | Grant | Ask the owner. |
| feat/gateway-hub-owner-login | 2026-09-23 | Grant | Ask the owner. |
| feat/owner-pairing-no-tofu | 2026-09-23 | Grant | Ask the owner. |
| feat/windy0-gateway-deploy-package | 2026-09-23 | Grant | Ask the owner. |

## Done

- 2026-11-04: New dates sent to Boss at 15:33Z (16 lines, old -> new).
- 2026-11-04: #687 got Mail's row-4 items at e075fa4: the Russ/Alex draft line and the mailbox count. Doc tests 441/441.
- 2026-11-04: W0 fleet-say swapped to f5e1d8de (backup kept). Tests 32/0 on W0; a proof relay succeeded; BOARD BEFORE and AFTER posted.
- 2026-11-04: OC5 sleep proof (A) PASS. The agent re-claimed before the message and gave one correct answer across the move (BOARD AFTER 14:16:57Z).
- 2026-11-04: Admin event declarations answered for 4 families: command.invoked, fire_drill, continuity.score, and the envelope.
- 2026-11-04: #700 `windy home pair` (Connect door, DARK) merged at eb80387d.
- 2026-11-03: #696 sync position, #697 Windows ACL, #698 runtime re-claim, #699 DB location and #701 turn-tail guard merged.
- 2026-11-03: call_api lab PASS (steps 7b-7f, 8, 9). The synthetic owner was purged.
- 2026-11-03: #679 lab PASS (Chat ok 0->1).
