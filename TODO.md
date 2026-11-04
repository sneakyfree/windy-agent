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

**Open PRs (#712 merged master in at bfc0a4ac), waiting on CI (busy Veron runners) and the stamps. Merge each ONLY on the gate above.**
- #717 TODO.md (this file): the 11-04 evening moves. TODO-only: a lane reader (Windy Calendar) + Boss's stamp.
- #709 retire latch + 60 s probe (N = 75 s; lines settled by Boss + Hub). Hub NOT OK on ee093be3 (by message, before 22:20Z): fixed at
  b130f948 (11-04 22:20Z): probe on every claim outcome but CONFLICT; passport from the token's sub; refused forced refresh
  + key_revoked mint back off 60 s -> 1 h (new kid at once; retired/suspended keep 60 s); ept_refresh lifts the stop only on
  a usable token; home pair sets a key Eternitas lists revoked aside (renamed, never deleted). ONE new line for Hub #9:
  "This computer's old key was turned off, so it was set aside. Starting a new pairing." Hub re-read asked.
- #720 home pair: Hub's #700 follow-ups 0-6 + bring-home already_external lines (1.1 H7) + Hub's #703 NaN nit. Opened
  11-04 22:24Z at ac5cb466 (stacked on #703, merged; master merged in). 6 new lines for Hub #9 (verbatim in the PR body).
  Due Fri 11-06.
- Hub's nits carried from his 11-04 stamps (follow-up PRs, not blocking): #706 when the current model is ALSO unavailable,
  say "Windy Mind will pick one for me" instead of "I'm still answering with X"; #715 gate the event with the same once-key
  as the log line; #719 confirm rev.7 promises ONE text block (ask Hand/contracts #278).
- #711 QA battery isolation + windyfly.lab_guard: CI failed 2 tests on d51ab719; fixed at 8ee4d8f9 (11-04 21:28Z: lab_guard
  prints -> sys.std*.write; harness logs out of a shared /tmp file into $STRESS_HOME), then be891a7e (Hub MEDIUM:
  mkdir the stress home 700 + empty guard). Hub re-read asked; Boss re-arm asked.
- #712 Calendar 422 idempotency_key_reused sentences + book() in_progress = still working (Calendar #127; Boss OK'd the
  words by message ~21:30Z). Head bfc0a4ac (master merged in: conflict with #704). Hub re-stamp asked. Must merge before Calendar deploys
  windy-calendar #118 (Calendar lists "#712 merged" as its precondition).

**Built, stacked on #720: open after #720 merges (merge master in, run the touched tests, push).**
- Desktop-door installer `windy home install --ticket - [--json-status]` (branch feat/home-install-desktop, eae3daf
  after master merged in 11-04 22:23Z, conflicts kept the branch's extra_headers; worktree wt-install, 17 tests). DARK.
  PR by Fri 11-06; end-to-end lab Wed 11-11 (constraint: Hub's desktop-door PR, DARK Fri 11-06).

**Thursday 11-05**
- Connect pairing lab #1, 13:00-14:00Z (Connect leads; headless browser plays the phone). Prep done:
  labs/connect-1/run-leg.sh (computer B = Veron) and GO-DRAFTS/LAB-connect-1-20261105.md. Still to do: Boss's GO for
  the prod flag window, set up OC5 home /Users/lab171d/fly-lab/pair-a (computer A), add the lab checkout at
  labs/connect-1/agent, post the BOARD BEFORE line. Leg 4 = ETERNITAS_URL=http://127.0.0.1:9 for ONE process only.
- CL5a Zero owner-call flip, 17:20-17:45Z. Hub OK (3 conditions in GO-DRAFTS/CL5a-zero-owner-call.md). Boss GO
  requested on the BOARD 15:59:36Z, not yet given. About 17:15Z: tell Windy Call "about to flip"; flip only on its GO.

**Sat 11-07: lab agent for Windy Mail's API deploy AFTER (mine; Mail asked 11-03 ~21:30Z, confirmed 11-04 ~19:50Z, exact time not logged)**
- Hatch about 11:10Z (Mobile's plan 19:53Z: WDA + owner sign-in 11:00Z; Boss option (b), BOARD 2026-11-04T19:52:46Z; GO 19:49:59Z item 7, correction 19:51:25Z):
  the MAC MINI Windy Mobile makes the synthetic probe.windyword.ai owner Fri (lab.register_kept, windy-pro-mobile
  #393; dated name + lockbox key NAME to me by Fri 18:00Z; fallback = Hub via verify-email-probe) and its PHONE
  [OWNER MADE EARLY, Mobile 11-04 22:12:21Z: mobile-lab-mail-after-owner-202611042211@probe.windyword.ai, lockbox
  NAME LAB_MAIL_AFTER_OWNER_PW_20261104 (not mine to read: the phone hatches), windy_identity_id
  f502136a-aec0-4d1d-b85d-8920495a0505; login 200. The Fri 18:00Z wake now only needs a glance.]
  hatches after its 1-6 AM ET window, writing the fresh owner token file (path + second named). I NEVER sign in or
  type as the owner (Boss: no exception, the hatch is a consent surface, contracts #278). FRESH lab agent on OC5,
  windyfly master (has #693), WINDY_MAIL_EPT_AGENT unset, lab guard (#711), house lanes only (Boss 17:01Z).
  Delete-only-what-this-run-created guard on every cleanup. Send Mail the passport suffix.
- From about 11:20Z, right after the hatch, on Mail's cue (Mobile reports what lands) (Mail's deploy stays 10:00Z), all to
  mobile-gate-check+journey-sent@windymail.ai: (1) one legacy-EPT read
  (my_mailbox) accepted; (2) the SAME send twice with the SAME key = 1 arrival, 2nd has Idempotent-Replayed: true;
  (3) one POST /api/v1/send/birth-announcement; (4) one send with its own body_html; (5) one text-only send. Mail
  also checks the agent-welcome mail in the new inbox.
- 15:15-15:30Z, after Mail flips WINDYMAIL_EPT_AGENT_ENABLED=true: set WINDY_MAIL_EPT_AGENT=1 on the lab agent only;
  one mode-B read + one send, both succeed with NO legacy retry (log shows it). Then BOARD AFTER.

- Thu 11-05 15:00-15:30Z: Code mode-B LAB for #713 (Boss GO 2026-11-04T20:54:50Z, moved by Boss 20:56:49Z after my
  20:55Z slip, BOARD 20:56:14Z). Hub OK by message on labs/code-modeb/code-modeb-lab.py at sha256 3372a2a8...4a58 (11-04 ~21:00Z, exact time not logged); at 14:50Z re-hash both copies + send the sha to Code (Code starts its builder only on it). Run on Windy 0
  from ~/.local/share/zero-lab (agent-713 = archive of 72c464ba), `check` first, BOARD BEFORE, steps 1-3 (on), step 4
  (off) on Code's word, BOARD AFTER, then remove agent-713 + the script. Zero untouched; no model calls.

**Due later this week**
- Key rotation by the agent itself: PR #714 OPEN 11-04 21:11Z (head f433b493 after Hub's 2 rounds: pending key saved before register, lost answers settled from the public list; WINDY_AGENT_KEY_OPS=1; Hub re-read asked 21:23Z; was due Tue 11-10; follow-up PR: prune .bak copies holding retired keys; rule OK'd by Hub 11-04 (plus .bak-ept-/.bak-deregister- at 0600, >30 d pruned, newest per kind kept; zeros+fsync before unlink; counts-only logs). BUILT on branch feat/credentials-bak-prune, head 18e01191 (stacked on #714), Hub read asked 22:07Z; open the PR after #714 merges; needs Boss's OWN stamp (it deletes secrets)) (Boss YES, BOARD 2026-11-04T19:49:59Z item 8; eternitas #302
  agent-key-ops.v1, Eternitas deploys Mon 11-09): a key-only Fly registers its new key with an Eternitas-Agent-Proof
  from the old one (two challenges), then the old key retires ITSELF; handles 401 invalid_agent_proof, 409
  last_active_key / too_many_active_keys, 403 not_this_key.
- QA battery deploy after #711 merges: ONE Boss GO (update ~/.local/share/zero-lab/agent + venv; install script + unit
  with InaccessiblePaths; dry run SKIP_HARNESS=1 pasting "lab guard: ok" + one deliberate REFUSED; re-enable the
  timer; BOARD BEFORE/AFTER). The timer on Windy 0 is DISABLED since 11-04 16:55Z.
- Consent surface: an MCP tool answering `windy_consent_surface` is final (no retry; the person must act). PR #719
  OPEN 11-04 21:54Z (head d8407389; Hub stamped + #9 11-04 night; Boss stamp on green asked); merges after windy-contracts #278 (rev.7 MUST).
- /model: read `outside_ai_label` live from catalog/me once Mind's pin is live (Sun 11-08 17:00Z); vendored = fallback.
- Fly mode-B for Search (EPT+agent aud windy-search + DPoP on POST /v1/search, /web/fetch), DARK, flag
  WINDY_SEARCH_EPT_AGENT: Search answered all 6 (11-04 evening, exact time not logged; windy-search #129 @ f78b05c; notes
  GO-DRAFTS/SEARCH-modeB-answers-20261104.md; wrong_token_type = fall back at once; 502 render failed = stored EPT).
  PR #716 OPEN 11-04 21:35Z (head 1de25f67; Hub OK + Search 2nd read ~21:46Z; Boss stamp asked).
  FLIP LIST (Search's nits via Hub): (1) drop the 502 render-failed stored-EPT send the hour Hand's acting-passport
  path is live (else a real render failure doubles); (2) before the flip, one INFO line at startup naming the Search
  base used (htu follows WINDY_SEARCH_BASE_URL; a non-default base = every proof counted bad). Search mode-B live Wed 11-11 13:00Z; flip = own GO + lab.
- agent.db_path_ambiguous ON: PR #715 OPEN 11-04 21:21Z (head ab30825c; Admin declared live 21:16Z, HQ 7d9138e1; Hub stamped 11-04 night; Boss stamp on green).
- windycode_web mode B (aud windy-code + DPoP), DARK: PR #713 OPEN 11-04 20:45Z (head 72c464ba since 20:51Z, test-only move; Hub re-read asked; Fly flag
  WINDY_CODE_EPT_AGENT=1), built to Code's 5 answers (windy-code-web #96 @ 8740b593; notes in
  GO-DRAFTS/CODE-modeB-answers-20261104.md). Hub read asked 20:46Z; watcher armed. If #96's review changes an
  answer, #713 changes the same hour. Hub re-stamp at 72c464ba (20:51:58Z, read back). Code lab needs Code e6cf623 (passes no_forwardable_credential) live too. Switch-on = Code's lab with this build + Boss GO + Kit 0 slot + KE5
  (Sites); flip checklist in the GO-DRAFTS file (WINDY_CODE_WEB_URL must equal the builder's public_base_url).
- Wire Fly to Hub's "my Windy" GET (my-windy.v1, aud windy-hub) the day Hub's account-server code merges (Fri 11-06).

## Waiting on

- **Code:** IDE agent-bus mode-B shape is FINAL (11-04 evening, exact time not logged, Eternitas ruled; GO-DRAFTS/CODE-modeB-answers-20261104.md):
  htu "urn:windycode:agent-bus" on every OS, htm AUTH, EPT+agent aud windy-code, proof in auth field "dpop". Fly's
  windycode.py bus client: DARK PR #718 OPEN 11-04 (head 347e5e12 after Code's 2nd read: no code = an old IDE = fall back;
  key on "code", new connection per retry; Hub stamped 11-04 night; Boss stamp on green; was due Fri 11-06); Code's IDE dark build Mon 11-09; lab after
  both. Hard constraint = legacy EPT cutoff 2027-01-04.
- **Code:** the 5 mode-B answers ARRIVED early (11-04 ~20:00Z, exact time not logged) -> #713; none changed in #96 review; no_forwardable_credential shape answered. Prod URL confirmed = Fly default. LAB builder URL known (http://100.110.135.105:18930, in labs/code-modeb/code-modeb-lab.py; Tailscale only).
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

- Purge the TRGE lab agent on OC5 at Fri 11-06's passport purge (tmux -L flylab as lab171d). Mail doesn't need it
  (Mail's Sat AFTER uses a fresh lab agent, see Now).

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

- 2026-11-04: MERGED on Hub + Boss stamps: #703 23af853c (pairing progress gate), #705 ae09254a (Vault revocable leases),
  #706 66ee6ad6 (/model refused switch), #707 20a60ac2 (web_search key-only line), #708 777de1be (no checkout leaks).
  #706-#708 first hit GitHub's "base branch was modified" race, re-queued one after another on the same heads.
- 2026-11-04 night second reads: WindyCloud #237, windy-translate #47, windy-mind #570, Windy-Clone #138, windytalk #147
  (2 switch-on MEDIUMs carried to Talk by Hub); windy-contracts #284 LN1 OK from the Fly side at 2b64206f (contact texts
  = no turn until Hub rules); windy-code-web #98 TODO-only, windy-agent/approved posted at 67aa0fbe.
- 2026-11-04: Hub #9 OK on windy-fly-site #22 (68d3cf66, deletions) and #21 (2112d8e6, dark until 'all' flip + F6 +
  Grant); both still need Boss's GO for their own daytime slots. Boss's Traveler correction (BOARD 22:20:06Z) relayed;
  Traveler's #14 already points at app.windyword.ai/translate?door=windy_traveler.
- 2026-11-04: #702 TODO.md rewrite MERGED 63ca046b. #710 a real passport is never marked synthetic MERGED 442b4450.
- 2026-11-04: #687 Traveler links: Boss (A) KEEP app.windyword.ai/translate (BOARD 21:13:51Z); relayed to Traveler.
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
