# TODO: windy-agent (Windy Fly)

Owner: the Windy Agent lane (Veron). Boss's rule, 2026-11-04:
- Read this file first, every session. Add every promise here in the same turn.
- A line leaves only into Done (shipped and tested) or by a Boss BOARD line.
- Never delete an unmerged branch without its owner's verdict. Tag it `archive/<branch>-<date>` first.
- Dates assume CONTINUOUS work (Grant, via Boss, 11-04). A date more than 5 days out names ONE hard constraint.
- If a PR waits more than 2 h for Hub's read, ask a second qualified reader (Admin, Cloud or Word) and tell Boss.
- Merge gate: windy-hub/approved + windy-boss/approved on the EXACT head + every CI context green; `--match-head-commit`;
  never force. Veron load rule: no heavy job while /proc/pressure/io avg60 >= 30; heavy work goes through veron-heavy.

Last full rewrite: 2026-11-04 18:35Z (before Grant's restart of every Veron Claude).

## Now

**Open PRs (#712 may need a rebase now that #704 merged: both append to tests/test_windy_calendar.py), waiting on CI (busy Veron runners) and the stamps. Merge each ONLY on the gate above.**
- #702 TODO.md (this file). Hub OK on 447b5aed. When it merges, this rewrite goes in a follow-up PR.
- #703 pairing progress gate (pre-flag; must merge before Connect's flag 11-10). Hub #9 done; head 539698d5.
- #705 Vault: release only revocable leases.
- #706 /model never confirms a switch Mind refuses (Hub #9 done; Mind's label vendored + pinned).
- #707 web_search on a key-only paired computer says plainly it isn't available yet (Boss + Hub #9 OK).
- #708 the test suite never writes into the checkout (a guard names any test that still does).
- #709 retire latch + 60 s probe (N = 75 s; all lines settled by Boss + Hub; H13a move line only on move_aborted).
- #710 a real passport is never marked synthetic, even a hand-set header (Hub OK at bb4f55c6).
- #711 QA battery isolation + windyfly.lab_guard (Hub OK at d51ab719).
- #712 Calendar 422 idempotency_key_reused sentences (Hub OK at 4d124b43). Must merge before Calendar deploys
  windy-calendar #118 (Calendar lists "#712 merged" as its precondition).

**Built locally, stacked on #703: open after #703 merges (rebase each onto master, run the touched tests, push).**
- #700 follow-ups 0-6 (branch fix/home-pair-followups, fea048c, worktree wt-followups): partway bring-home line;
  Ctrl-C at y/N = cancelled 130; Ctrl-C while registering keeps the key; ConnectError = the running-nowhere line;
  the marker carries the memory outcome; the memory line names the DB path; env-file passport test; bring-home
  already_external state lines (home 1.1 H7). New person lines need Hub #9. Due Fri 11-06.
- Desktop-door installer `windy home install --ticket - [--json-status]` (branch feat/home-install-desktop, 8830c79,
  worktree wt-install, 17 tests; stacked on the follow-ups). DARK. PR by Fri 11-06; end-to-end lab Wed 11-11
  (constraint: Hub's desktop-door PR, DARK Fri 11-06).

**Thursday 11-05**
- Connect pairing lab #1, 13:00-14:00Z (Connect leads; headless browser plays the phone). Prep done:
  labs/connect-1/run-leg.sh (computer B = Veron) and GO-DRAFTS/LAB-connect-1-20261105.md. Still to do: Boss's GO for
  the prod flag window, set up OC5 home /Users/lab171d/fly-lab/pair-a (computer A), add the lab checkout at
  labs/connect-1/agent, post the BOARD BEFORE line. Leg 4 = ETERNITAS_URL=http://127.0.0.1:9 for ONE process only.
- CL5a Zero owner-call flip, 17:20-17:45Z. Hub OK (3 conditions in GO-DRAFTS/CL5a-zero-owner-call.md). Boss GO
  requested on the BOARD 15:59:36Z, not yet given. About 17:15Z: tell Windy Call "about to flip"; flip only on its GO.

**Due later this week**
- QA battery deploy after #711 merges: ONE Boss GO (update ~/.local/share/zero-lab/agent + venv; install script + unit
  with InaccessiblePaths; dry run SKIP_HARNESS=1 pasting "lab guard: ok" + one deliberate REFUSED; re-enable the
  timer; BOARD BEFORE/AFTER). The timer on Windy 0 is DISABLED since 11-04 16:55Z.
- Consent surface: an MCP tool answering `windy_consent_surface` is final (no retry; the person must act). PR Fri
  11-06, once Hub's hands.mcp.v1 carries the MUST.
- /model: read `outside_ai_label` live from catalog/me once Mind's pin is live (Sun 11-08 17:00Z); vendored = fallback.
- Fly mode-B for Search (EPT+agent aud windy-search + DPoP on POST /v1/search, /web/fetch): PR Thu 11-12. Constraint:
  Windy Search mode-B live Wed 11-11.
- windycode_web: aud "windy-code" + DPoP, DARK by Tue 11-17 (the builder still refuses EPT+agent: 401 then legacy 200,
  seen 11-04 18:02Z). Constraint: Windy Code's builder accepting mode B.
- Wire Fly to Hub's "my Windy" GET (my-windy.v1, aud windy-hub) the day Hub's account-server code merges (Fri 11-06).

## Waiting on

- **Grant:** his own typed OK in the Windy Agent terminal before the line "Read TODO.md first; add every promise to
  it the same turn." goes into CLAUDE.md in windy-agent, windy-fly-site and windy-0-soul. Asked 11-04 11:45Z.
- **Boss:** GO for CL5a (Thu 17:20Z) and for the Connect lab's prod flag window (Thu 13:00Z). Nudge Thu 11:00Z.
- **Boss:** stamps on #702-#712 (his watchers stamp on green); decisions on #687 (ONE-WINDY-PLAN).
- **Hub:** code reads / windy-hub/approved on #703, #704, #705, #706, #707, #708, #709 when green.
- **Hub:** the desktop-door code (DARK Fri 11-06); revoke reason `move_aborted` for H13a (asked Eternitas too).
- **Eternitas:** the exact refusal code for a revoked agent key; `retired_until` in the agent_retired refusal (Q9).
- **Mind:** "test passport = house keys only" rule (Boss picks A blanket / B narrow).
- **Windy Talk:** claim Talk + talk-site by Thu 11-05 18:00Z; else I write their TODO.md by Mon 11-09 12:00Z.
- **Capture crew plan (Boss, Mon 11-09):** may name an Agent part; read it that day.

## Later

- Purge the TRGE lab agent on OC5 at Fri 11-06's passport purge (tmux -L flylab as lab171d).

## Branches

Unmerged remote branches on 2026-11-04. Verdicts: mine given here; other owners' by Boss's date, Fri 11-13.

| Branch | Last commit | Owner | Verdict |
|---|---|---|---|
| archive/gauntlet/* (12 branches) | 2026-05-21 | Grant | Already archived. Keep. |
| docs/one-windy-plan | 2026-11-04 | Windy Agent | Open PR #687. Keep. |
| docs/todo-md, feat/pair-progress, fix/calendar-book-key, fix/vault-release-revocable, fix/model-switch-refused, fix/search-key-only-plain, test/no-checkout-leaks, feat/standing-latch, fix/synthetic-never-real-passport, fix/qa-battery-isolation, fix/calendar-key-reused | 2026-11-04 | Windy Agent | Open PRs #702-#712. Keep until merged. |
| wip/windy0-crash-windy-agent-20261020 | 2026-10-20 | Windy Agent | KEEP. Rescue snapshot of every Windy 0 local branch after the 10-19 crash ("Do not merge"); 53 of 54 commits are pre-rebase copies not on master by patch. Delete only with Boss's OK, after tagging archive/. |
| wip/windy0-crash-windy-agent-20261020-b | 2026-10-20 | Windy Agent | KEEP. Rescue part 2 (Windy 0 standalone clones; "Do not merge"); 8 of 16 commits not on master by patch. Same rule. |
| docs/memory-options | 2026-11-02 | Grant | Ask the owner. |
| feat/lean-fly | 2026-11-02 | Kit OC1 | Ask the owner. |
| release-0.7.x | 2026-11-02 | Grant | Release line. Ask the owner. |
| release/v0.7.7 | 2026-10-22 | Kit OC1 | Release line. Ask the owner. |
| release/0.7.6 | 2026-10-04 | Grant | Release line. Ask the owner. |
| hotfix/zero-db-lock-7311e0e | 2026-10-16 | Grant | Ask the owner. |
| fix/backup-key-required | 2026-10-10 | Grant | Ask the owner. |
| feat/mind-helper-wiring | 2026-09-30 | Grant | Ask the owner. |
| feat/snapshot-key-flagged | 2026-09-30 | Grant | Ask the owner. |
| feat/gateway-hub-owner-login | 2026-09-23 | Grant | Ask the owner. |
| feat/owner-pairing-no-tofu | 2026-09-23 | Grant | Ask the owner. |
| feat/windy0-gateway-deploy-package | 2026-09-23 | Grant | Ask the owner. |

Local-only branches (not pushed): fix/home-pair-followups, feat/home-install-desktop (see Now).

## Done

- 2026-11-04: #704 Calendar book_appointment deterministic key MERGED 68d7d859 (Hub OK + Boss stamp + green).
- 2026-11-04: Windy Code's prod builder #92 AFTER proof: one zero-lab get_capabilities call, 18:02:07Z, final 200 with
  a real result (builder log: 401 on EPT+agent, then 200 on the legacy retry).
- 2026-11-04: windy-qa-battery timer on Windy 0 disabled (Boss GO 16:54Z; BOARD BEFORE 16:55:03Z, AFTER 16:55:12Z).
  Root cause of Zero's "synthetic" Mind calls found (the 11-01 battery minted as Zero; 29 calls on Grant's keys,
  $0.0009). The 10-11 rule is a HUB ruling (BOARD 2026-10-11T15:44:17Z), not Grant's.
- 2026-11-04: windy-contracts #270 (OK on d3ec6587), #273 (answers + the H13a revoke-reason gap), #274 (no Agent part).
- 2026-11-04: windy-fly-site #19 + #20 and windy-0-soul #1 (TODO.md) merged after Hub's read.
- 2026-11-04: New dates sent to Boss at 15:33Z. #687 got Mail's row-4 items (e075fa4).
- 2026-11-04: W0 fleet-say swapped to f5e1d8de; OC5 sleep proof (A) PASS; Admin declarations answered.
- 2026-11-04: #700 `windy home pair` (Connect door, DARK) merged at eb80387d.
- 2026-11-03: #696, #697, #698, #699, #701 merged; call_api lab PASS; #679 lab PASS.
