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

**Boss 11-05 ~10:12Z (relayed; stamps visible on GitHub):** #719 = KEEP. Connect lab 13:00Z GO stands (BOARD 11-04 20:26Z,
lab owner only, flag deleted 14:00Z). Code mode-B lab 15:00-15:30Z GO stands: CONFIRM Hub read the fixed lab script first.
CL5a 17:20Z: send Boss its BEFORE (pin, what changes, Hub's OK line, rollback) BY 15:00Z, then he posts the GO.
  PLAN: ~14:20Z (after the Connect lab's 14:00Z flag delete) read Zero READ-ONLY: both services active, the sha (draft
  pin 633cfc85 or later, must contain dd38ea0), WINDY_OWNER_CALL unset, 0 ERROR today; then fill GO-DRAFTS/CL5a from
  that and send the BEFORE to Boss by fleet-say by 14:45Z. The 11:00Z nudge is moot (Boss answered at ~10:12Z).
windy-fly-site #22 16:00Z GO stands.
- CONNECT LAB #1 DONE 13:00-13:07Z (closed early by Connect): legs 1, 2, 5 PASS; leg 3 = a real gap (Hub ticket 201,
  so a SECOND agent hatched, not a move); leg 4 not run (its own rotate lab later, Connect calls). AFTER BOARD 13:09:49Z,
  all 3 homes shredded; Hub purges ET26-TEST-TETP + ET26-TEST-P0UG. Build was master b0c98dd0 (Connect OK).
- ASKED Hub 13:10Z (privacy): the plaintext memory copy memory-<id>.ndjson left in the state dir after a VERIFIED import
  (bring_home.install_memory). Options (a) keep, (b) delete after a verified import (my pick), (c) TTL. Build what Hub
  picks; cc Windy Connect on the answer (its CLI _save_memory does the same, and it will match).
- ALERT GAP (Boss BOARD 13:06:36Z, Cloud 13:09:08Z): windy-agent-journey (Veron user timer, every 2 h) has FAILED every
  run in the journal (since 11-03): Zero's reply header is now "[🪰 Windy Fly · <date> · 🟢 98% · cloudflare/@cf/zai-org/
  glm-4.7-flash]", which the parser doesn't read; and the brain is NOT Claude. Keeps windy-uptime heartbeats-veron red.
  DUE Fri 11-06 18:00Z: fix the parser for the new header, AND find out from Mind whether a NON-owner probe DM to Zero
  is meant to get the Cloudflare GLM lane (then the journey expects it) or Claude (then it's a real demotion: tell Boss).
  Else retire the expectation by a windy-uptime PR (Cloud reads).
  FOUND 13:15Z (Mind's Kit 0 read + my Windy 0 read): NOT a parser-only issue. Mind's /self for NPD1 = picked_by owner,
  model GLM, from the AGENT-scope grant default set 10-24 01:13:09Z during Mobile's device tests (caller unrecorded);
  0 agent switches; live since ~10-29 (Mind #530). Zero's config = claude-opus-5-5. So Grant's own DMs likely get GLM.
  ASKED BOSS 13:15Z (fleet-say): is GLM intended? Clearing/setting the grant = Boss word or Grant's phone. Journey stays
  RED on purpose until then; parser fix for the new bracket header comes with whatever Boss decides. Tell Cloud.
- #731 OPENED 13:14Z (Hub's (b): wipe the plaintext memory copy after a VERIFIED import; keep 0600 + reason line when not
  proven, incl. unverified per Hub). Head 3b93a59d; Hub STAMPED 13:16Z; Boss asked 13:17Z.
- GRANT DECIDED (Boss BOARD 13:17:41Z): Zero = claude-sonnet-5-5; Mind writes it today. Journey stays RED until Mind's
  AFTER. Then: mv jobs/windy-agent-journey.sh.new over the .sh (staged 13:2xZ: bracket header, model last; expects
  claude-sonnet-5-5), run the service once by hand, and post the first green run on the BOARD. Tell Cloud.
- Journey: if Boss's GLM decision runs past Fri 11-06 18:00Z, open a windy-uptime PR adding hold_until + hold_reason for
  windy-agent-journey in checks.json naming that wait (Boss's line allows it; Cloud reads).
- MORNING SUMMARY SENT 10:11Z (fleet-say). #726 MERGED 52a9fdd9, #727 MERGED 1b2ba9cb (~10:12Z).
- #725 MERGED 92b840eb (10:14Z), #717 MERGED 246b6929 (10:15Z). #709 stamped + green + CLEAN, its watcher polling (ends ~10:24Z).
- Opened #728 (#726 follow-ups: finish a stopped round, Windy Word way back, the cancel line, https-only hub check),
  3b169441, 10:16Z. Needs Hub (+#9 on 2 new lines) + Boss.
- SLIP 10:15Z: rebuilt fix/home-install-finish on master after #726's squash merge and pushed it with --force-with-lease
  (my own branch, no PR yet; nothing lost: same commits cherry-picked; old head kept as tag
  archive/fix-home-install-finish-pre-rebuild-20261105 = 775b98a). The rule is NEVER force-push: next time push the
  rebuilt branch under a NEW name instead. Reported to Boss.

**Morning summary to Boss by 11:00Z 11-05 (Boss 21:07Z rule: one line per item moved). Running list:**
- Merged overnight: #712 f164694f, #713 53da05ef, #715 c674813f, #716 0fcc1e75 (dark), #718 bf950072 (dark), #721 399ee555 (/model: "Windy Mind will pick one for me" when the current model is down too), #711 e17884b7 (lab guard), #714 d670d59b (key rotation, dark), #724 a9863c42 (my-windy, dark), #720 e054b5ec (home pair follow-ups), #719 acc24cd3 (see the slip below), #722 c7c6989e (db_path_ambiguous event once), #723 34a58e44 (Search base line, dark).
- Opened #722 (Hub's #715 once-key nit), 2c80a457, 03:23Z; Hub STAMPED; Boss asked.
- Second reads: windy-contracts #284 OK 2b64206f; windy-code-web #98 at 67aa0fbe; windytraveler-site #25 at
  83db250a; windy-code #102 matches #718.
- Watcher fix: mergeq-strict.sh stops after 6 h (72 polls), not at its timeout. Re-arming each one at the same head.
- CI: cancelled the 01:13Z + 02:27Z master sets (8 runs) on Boss's GO 03:28:03Z; BOARD 03:30:13Z.
- #709 follow-ups written: fix/standing-followups 74ab21e (Hub's set-aside nits + his #294 rulings: owner-only reason, revoked checked hourly); no PR until #709 merges.
- Opened #723 (Search flip-list item 2: startup line naming the Search base, dark), f88d6813, 03:31Z.
- Hub stamped #722 at 2c80a457; #723 at 722632c8 (Search OK too).
- Opened #724 (your-windy: my-windy.v1 plan + platforms, dark), 7dc542a9, 03:37Z; Hub STAMPED 03:41Z.
- Opened #725 (prune old credentials/env backups, Hub's #714 ask), 05:58Z; Hub STAMPED 1cfddfd7, his nit 1 (fail safe:
  only retired/revoked lets a backup go) -> 95690622, Hub re-STAMPED; Boss's own read asked.
- Opened #726 (windy home install --ticket -, desktop door, dark), 5a55d72f, 06:01Z; Hub STAMPED.
- #726 follow-up written per Hub's ruling: fix/home-install-finish 86dfa32 (no PR until #726 merges).
- #725 Hub re-STAMPED at 95690622.
- #719 MERGED acc24cd3 06:53Z, BEFORE contracts #278 (my order slip; reported to Boss + Hub on the BOARD 06:53:51Z;
  keep or revert = Boss's call). #709 hit a merge conflict -> master merged in, fc088209; Hub re-STAMPED 06:55Z; Boss re-stamp needed.
- Opened #727 (https-only hub_url for every caller, Boss's item), aa0b4624, 06:11Z; Hub STAMPED + #9 OK.
- Contract read: windy-contracts #294 (agents-retire 1.1.0) agent_runtime row: OK + 4 line notes to Hub (07:32Z).
- Read: windy-pro #1760 (pickup proof check) @3e9d31d2 OK + 1 WIRING item (Router req.path is mount-relative: use
  baseUrl+path) -> Hub put the rule in the doc at 8c29e48b, slice 2 adds the real-mount route test + refuses the door
  without Redis; #1761 (owner cancel) @c3e671d2 OK. Cancel line added on fix/home-install-finish 775b98a (Hub #9 when PR opens).
- Read: windy-pro #1758 (home 1.1 slice 1: already_external state) line posted at 8b6c7c21: OK + ONE item before its flag
  (the Eternitas key list is cacheable 300 s: a stale list = move_unfinished = a rotate with no question; send no-cache).
  Hub fixed it at 882ce284 (no-cache pinned; skew margin since-5min widens running_elsewhere = safe; paged = unknown): re-read OK.
- Second read: windy-chat #564 (deploy-svc.sh clamav untouched by id) windy-agent/approved at cb384fb2, 06:08Z.
- Boss offline 05:59Z-~10:00Z (weekly limit): asks parked on the BOARD 05:59:40Z; re-send at 10:00Z.
- #719 rev.7 nit answered from the contract text: no change needed.

**Open PRs (#712 merged master in at bfc0a4ac), waiting on CI (busy Veron runners) and the stamps. Merge each ONLY on the gate above.**
- #717 TODO.md (this file): the 11-04 evening moves. TODO-only: a lane reader (Windy Calendar) + Boss's stamp.
- #709 retire latch + 60 s probe (N = 75 s; lines settled by Boss + Hub). Hub NOT OK on ee093be3 (by message, before 22:20Z): fixed at
  b130f948 (11-04 22:20Z): probe on every claim outcome but CONFLICT; passport from the token's sub; refused forced refresh
  + key_revoked mint back off 60 s -> 1 h (new kid at once; retired/suspended keep 60 s); ept_refresh lifts the stop only on
  a usable token; home pair sets a key Eternitas lists revoked aside (renamed, never deleted). ONE new line for Hub #9:
  "This computer's old key was turned off, so it was set aside. Starting a new pairing." Hub: items 1-5 hold; the line
  was false on N / passport mismatch -> fixed at 318bedd6 (22:29Z): set-aside only after every refusal + the yes; the
  rename never overwrites; AST pin at top level. Hub STAMPED 318bedd6 (#9 OK); Boss re-arm asked 22:30Z.
  FOLLOW-UP after #709 merges (Hub nit): after a set-aside, the phone-decline/expired lines (home_pair.py ~368) say
  "Nothing else was changed." (needs Hub #9); and if unlink fails after link, log it. WRITTEN 11-05 03:29Z: branch
  fix/set-aside-followups 58c10b4, now carried on fix/standing-followups 74ab21e (see below); open the PR when #709 merges.
- #720 home pair: Hub's #700 follow-ups 0-6 + bring-home already_external lines (1.1 H7) + Hub's #703 NaN nit. Opened
  11-04 22:24Z at ac5cb466. Hub NOT OK (double-pasted functions = mypy red; NaN/Infinity interval; bh line untrue;
  fail-open seal check) -> fixed at ba555bac (23:29Z); Hub STAMPED + #9 OK on both lines; Boss re-arm asked; CI = gate.
  The installer (wt-install) must merge #720's new head in before its own PR.
  Due Fri 11-06.
- Hub's nits carried from its 11-04 stamps (follow-up PRs, not blocking): #706 -> PR #721 MERGED 399ee555 11-05 04:40Z (2d991b44, opened 11-04 22:31:48Z;
  "Windy Mind will pick one for me" when the current model is down too; Hub stamped + #9 OK; Boss asked); #715 -> PR #722 MERGED c7c6989e 11-05 07:00Z; #719 confirm rev.7 promises ONE text block -> ANSWERED 11-05 03:34Z from #278 @ 259a1dc7: rev.7 fixes result_shape {ok,result,error} but not the MCP wrapping; #719 covers whole text / any block / structuredContent, so no change (told Hub; a rev.8 line is #278's owner's call).
- #711 QA battery isolation + windyfly.lab_guard: CI failed 2 tests on d51ab719; fixed at 8ee4d8f9 (11-04 21:28Z: lab_guard
  prints -> sys.std*.write; harness logs out of a shared /tmp file into $STRESS_HOME), then be891a7e (Hub MEDIUM:
  mkdir the stress home 700 + empty guard). Hub re-read asked; Boss re-arm asked.
- #712 MERGED 11-04 f164694f (Calendar told; Calendar's prod 422 arrives with ONE Pages slot after windy-calendar #130
  merges, migrations 0018+0019 first; Calendar tells me the day. Zero @633cfc8 (no #704) CAN reuse a key only if the model
  passes one (not advertised, not enforced): then today = false replay, after deploy = unworded 422 (truer). Told Calendar the
  deploy needn't wait for Zero's #712 pin; Boss decides). Was: Calendar 422 idempotency_key_reused sentences + book() in_progress = still working (Calendar #127, rebuilt on main as windy-calendar #130 @ 09958b6c, same answer shape; Boss OK'd the
  words by message ~21:30Z). Head bfc0a4ac (master merged in: conflict with #704). Hub re-stamp asked. Must merge before Calendar deploys
  windy-calendar #118 (Calendar's slot waits on #712 merged AND windy-calendar #130 merged).

**Desktop-door installer: PR #726 OPEN 11-05 06:01Z (5a55d72f; Hub STAMPED; Boss when back). Follow-ups asked of Hub:
HUB RULED 11-05 06:05Z (follow-up PR after #726 merges, branch fix/home-install-finish):
(1) #9 OK: the desktop human line = "Start the move again in Windy Word." (home pair keeps "Run this again for a new
code..."); --json-status codes unchanged. (2) home install FINISHES a same-passport marker like home pair: same passport
only (else another_agent_here); no new key, no kid line; only the steps home pair would finish; a spent-code /memory
fetch is skipped + reported, never retried; a stdin ticket is ignored on that path; --json-status {"event":"resumed"};
test: register ok, later step fails, re-run finishes with no new key and no code-needing call. BUILT: fix/home-install-finish 86dfa32 (pushed, no PR; stacked on
#726; 4 tests + the already_home name escaped); open after #726 merges, Hub re-read + Boss.**
- Desktop-door installer `windy home install --ticket - [--json-status]` (branch feat/home-install-desktop, eae3daf
  after master merged in 11-04 22:23Z, conflicts kept the branch's extra_headers; 05e71e4 after #720's ba555bac merged in 23:30Z; worktree wt-install, 17 tests). DARK.
  PR by Fri 11-06; end-to-end lab Wed 11-11 (constraint: Hub's desktop-door PR, DARK Fri 11-06).

**Thursday 11-05**
- 16:00-16:20Z windy-fly-site #22 deploy (deletions; Boss GO BOARD 2026-11-04T22:27:33Z; Hub #9 OK): merge on
  the gate, BOARD BEFORE, deploy, check the page, BOARD AFTER; rollback = the previous deploy. #21 stays unmerged until
  F6 + Grant. Wake 15:55Z armed.
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
- #709 follow-ups from the #294 read: Hub ruled (b) + the 1 h revoked back-off (contracts #294 now b58666ac). BUILT on
  fix/standing-followups 74ab21e (with the old set-aside nits cherry-picked); Hub said YES to both as built (neutral
  line for EVERY stop kind to non-owners; 3600 s revoked recheck); PR after #709 merges, then Hub #9 at its head.
- https-only hub_url() for EVERY caller (Hub's note on #724; Boss made it an item 11-05 03:43Z: a stored EPT must
  never go to a non-https host). hub_login.hub_url() refuses a base that isn't https, except localhost/127.0.0.1/::1,
  with a clear error each caller already shows (OAuth login, bring-home handover, hub_hatch, my-windy read). PR by
  Fri 11-06 12:00Z. Hard constraint: after Thu 11-05's Connect lab (13:00-14:00Z), which may point WINDY_HUB_URL at a
  lab host; check the lab's env first. Hub read (auth) + Boss.
  -> PR #727 aa0b4624 (11-05 06:11Z). Lab env checked: connect-1 never sets WINDY_HUB_URL and runs a pinned checkout,
  so the merge doesn't touch it. Hub STAMPED + #9 OK (both lines); Boss when back (~10:00Z).
  THEN (after #727 AND #726 merge): home install checks hub_url() before begin_local_key and shows the same line
  (today it ends as internal/HubUrlRefused after the key is undone). Fold into the fix/home-install-finish PR if timing allows.
- Key rotation by the agent itself: PR #714 OPEN 11-04 21:11Z (head f433b493 after Hub's 2 rounds: pending key saved before register, lost answers settled from the public list; WINDY_AGENT_KEY_OPS=1; Hub re-read asked 21:23Z; was due Tue 11-10; follow-up PR: prune .bak copies holding retired keys; rule OK'd by Hub 11-04 (plus .bak-ept-/.bak-deregister- at 0600, >30 d pruned, newest per kind kept; zeros+fsync before unlink; counts-only logs). BUILT on branch feat/credentials-bak-prune, head 18e01191 (stacked on #714), Hub read asked 22:07Z; open the PR after #714 merges; needs Boss's OWN stamp (it deletes secrets)) (Boss YES, BOARD 2026-11-04T19:49:59Z item 8; eternitas #302
  agent-key-ops.v1, Eternitas deploys Mon 11-09): a key-only Fly registers its new key with an Eternitas-Agent-Proof
  from the old one (two challenges), then the old key retires ITSELF; handles 401 invalid_agent_proof, 409
  last_active_key / too_many_active_keys, 403 not_this_key.
- QA battery deploy after #711 merges: ONE Boss GO (update ~/.local/share/zero-lab/agent + venv; install script + unit
  with InaccessiblePaths; dry run SKIP_HARNESS=1 pasting "lab guard: ok" + one deliberate REFUSED; re-enable the
  timer; BOARD BEFORE/AFTER). The timer on Windy 0 is DISABLED since 11-04 16:55Z.
- Consent surface: an MCP tool answering `windy_consent_surface` is final (no retry; the person must act). PR #719
  OPEN 11-04 21:54Z (head 59e880e7 since 22:29Z: the refusal counts in any one text block or structuredContent, Hub's rev.7 nit; Hub STAMPED; Boss re-arm asked); merges after windy-contracts #278 (rev.7 MUST).
- /model: read `outside_ai_label` live from catalog/me once Mind's pin is live (Sun 11-08 17:00Z); vendored = fallback.
- Search mode B: PR #716 MERGED DARK 11-04 0fcc1e75 (Search told). Fly mode-B for Search (EPT+agent aud windy-search + DPoP on POST /v1/search, /web/fetch), DARK, flag
  WINDY_SEARCH_EPT_AGENT: Search answered all 6 (11-04 evening, exact time not logged; windy-search #129 @ f78b05c; notes
  GO-DRAFTS/SEARCH-modeB-answers-20261104.md; wrong_token_type = fall back at once; 502 render failed = stored EPT).
  PR #716 OPEN 11-04 21:35Z (head 1de25f67; Hub OK + Search 2nd read ~21:46Z; Boss stamp asked).
  FLIP LIST (Search's nits via Hub): (1) drop the 502 render-failed stored-EPT send the hour Hand's acting-passport
  path is live (else a real render failure doubles); KEEP it until Search's own separate "live" message (Search 11-04
  night: that path's PR opens ~09:27Z, NOT in the Wed 11-11 slot unless Boss adds it); (2) before the flip, one INFO line at startup naming the Search
  base used (htu follows WINDY_SEARCH_BASE_URL; a non-default base = every proof counted bad). (2) -> PR #723 MERGED 34a58e44 11-05 08:10Z (opened 03:31Z at f88d6813; Search 2nd read fixed the words -> 722632c8; Hub re-STAMPED + Search OK at 722632c8; Boss ARMED at 722632c8, stamps on green). Search mode-B live Wed 11-11 13:00Z; flip = own GO + lab.
- agent.db_path_ambiguous ON: PR #715 MERGED c674813f; opened 11-04 21:21Z (head ab30825c; Admin declared live 21:16Z, HQ 7d9138e1; Hub stamped 11-04 night; Boss stamp on green).
- windycode_web mode B (aud windy-code + DPoP), DARK: PR #713 MERGED 11-04 53da05ef (Code told; Thu lab unchanged); opened 20:45Z (head 72c464ba since 20:51Z, test-only move; Hub re-read asked; Fly flag
  WINDY_CODE_EPT_AGENT=1), built to Code's 5 answers (windy-code-web #96 @ 8740b593; notes in
  GO-DRAFTS/CODE-modeB-answers-20261104.md). Hub read asked 20:46Z; watcher armed. If #96's review changes an
  answer, #713 changes the same hour. Hub re-stamp at 72c464ba (20:51:58Z, read back). Code lab needs Code e6cf623 (passes no_forwardable_credential) live too. Switch-on = Code's lab with this build + Boss GO + Kit 0 slot + KE5
  (Sites); flip checklist in the GO-DRAFTS file (WINDY_CODE_WEB_URL must equal the builder's public_base_url).
- Wire Fly to Hub's "my Windy" GET (my-windy.v1, aud windy-hub) the day Hub's account-server code merges (Fri 11-06).
  BUILT EARLY, DARK: PR #724 7dc542a9 (11-05 03:37Z; WINDY_MY_WINDY=1 + WINDY_YOUR_WINDY=1). Hub STAMPED 7dc542a9 (03:41:58Z); head sent to Boss
  for his read (auth-touching). Hub's Fri notes: (1) WINDY_MY_WINDY stays OFF everywhere until account-server accepts mode
  B (else a 401 + a re-mint each session); Hub tells me when his dark server code is up, then lab it.
  11-05 06:19Z: Hub's mode-B verifier is up DARK (windy-pro #1745 @413ba107); Fly proofs checked compatible (uuid4 jti,
  no ath, full-URL htu). Route up DARK: windy-pro #1746 @726b99bd (stacked on #1745);
  its body read against my_windy_facts = compatible (no-ms read_at_utc, dedupe, enums, plan regex). Not deployed: needs
  Admin read + Boss + a pin slot + an env flip GO. WAITING: Hub says a lab instance takes mode B -> lab #724 there only. (2) -> its own item under "Due later this week"
  (https-only hub_url()). Boss READ + ARMED #724 at 7dc542a9 (stamps on green). Lab it
  against Hub's dark server Fri. Follow-up: 401 agent_retired -> retire latch after #709 + agents-retire 1.1.

## Waiting on

- **Boss:** windy-0-soul #2 (RECOVERY.md) stamp at 2f2fb35b (Hub STAMPED 11-04 night; his MED fixed = backups OFF before a new
  machine's first start; LOW = stop the other unit while restoring). Docs only; any real act on Zero = Boss GO + Hub OK.
- **Code:** IDE agent-bus mode-B shape is FINAL (11-04 evening, exact time not logged, Eternitas ruled; GO-DRAFTS/CODE-modeB-answers-20261104.md):
  htu "urn:windycode:agent-bus" on every OS, htm AUTH, EPT+agent aud windy-code, proof in auth field "dpop". Fly's
  windycode.py bus client: DARK PR #718 MERGED 11-05 03:20Z bf950072 (Code told); opened 11-04 (head 347e5e12 after Code's 2nd read: no code = an old IDE = fall back;
  key on "code", new connection per retry; Hub stamped 11-04 night; Boss stamp on green; was due Fri 11-06); Code's IDE dark build UP EARLY 11-04: windy-code #102 @ 140bbac1 (WINDY_AGENTBUS_MODE_B), code table checked = #718, no change; lab after
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

- Zero re-pin (added 11-04 night): Zero runs windy-agent 633cfc8, without tonight's merges (#703-#708, #710, #712, #713:
  #704's deterministic booking key, #712's 422 words). Ask Boss for a GO (+ Hub OK) for ONE daytime slot to move Zero to a
  reviewed master commit, after Thu's labs, with BOARD BEFORE/AFTER and rollback = 633cfc8. Done means: the GO asked with
  the exact sha and the list of what changes for Zero; nothing moves without it.
- Sun 11-08 by 18:00Z: tell Windy Mind the passport id (id only) of ANY lab agent of mine still running after Mon 11-09
  11:00Z whose passport is not ET26-TEST- (Mind #567 pin: only ET26-TEST- or MIND_SYNTHETIC_IDS stay synthetic). Promised
  11-04 night; silence = nothing to add.
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
