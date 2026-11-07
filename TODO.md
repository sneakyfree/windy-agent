# TODO: windy-agent (Windy Fly agent runtime)

Owner: the Windy Agent lane (Veron). Rules (Boss 11-04; Grant 11-07 "TODO.md is our culture"):
- Read this file first, every session. A promise, date or GO that names this lane goes in here THE SAME DAY.
- A line leaves only into Done (shipped and tested) or by a Boss BOARD line.
- Never delete an unmerged branch without its owner's verdict. Tag it `archive/<branch>-<date>` first.
- Dates assume CONTINUOUS work. A date more than 5 days out names ONE hard constraint.
- If a PR waits more than 2 h for Hub's read, ask a second qualified reader (Admin, Cloud or Word) and tell Boss.
- Merge gate: windy-hub/approved + windy-boss/approved on the EXACT head + every CI context green; `--match-head-commit`;
  never force. Veron load rule: no heavy job while /proc/pressure/io avg60 >= 30; heavy work goes through veron-heavy.

**WE ARE LIVE, NOT A SANDBOX (Grant, standing rule, BOARD 2026-11-06T23:29:54Z).** Build, wire and ship for real at full
speed; don't hold work back because a stray visitor might have a rough time. What does NOT change: principle 1 (don't
break what's live; every change has a way back), principle 9 (say only what's true), consent is a human act, secrets
stay in the lockbox, and Boss's GO by sha for production. Grant's gates before the big marketing push, in order:
(1) Grant + a few friends say "really good, really solid"; (2) the back-end records work across the repos (CFO ledger,
Legal e-sign/archive/recall, Support email, smooth sign-up); (3) an attorney reviews the legal documents; (4) every
token on every platform is rolled (Vault leads); (5) then the marketing money.

Last full rewrite: 2026-11-07 00:01Z (catch-up, Boss BOARD 2026-11-06T23:57:40Z); updated 2026-11-07 01:53Z (line standard, Boss 2026-11-07T00:02:22Z).

## Now

Line standard (Boss BOARD 2026-11-07T00:02:22Z): - [ ] what · OWNER · ADDED · DUE · STATUS · SOURCE; Done = - [x] · DONE · PROOF.

**Sat 2026-11-07**
- [ ] Search #124 (EPT+agent on the bearer path): no code change (we send the typ EPT); run one journey after Search says it's live · OWNER Windy Agent · ADDED 2026-11-06 · DUE 2026-11-07 09:45Z · STATUS waiting on Search · SOURCE windy-search #124; Search 11-06
- [ ] Mail API AFTER lab on a FRESH lab agent (lab guard; WINDY_MAIL_EPT_AGENT unset; delete only what this run created). GATE ~10:45Z: no step naming the lab owner until a same-hour BOARD line (LIVE + test-tagged + durable) from Hub or Windy Fly Mobile; its phone hatches ~11:00-11:10Z (a consent surface: no lane signs in or types as the owner). From ~11:20Z on Mail's cue: (1) one legacy-EPT read; (2) the same send twice with the same key = 1 arrival + Idempotent-Replayed; (3) one birth-announcement send; (4) one send with its own body_html; (5) one text-only send. BOARD BEFORE/AFTER · OWNER Windy Agent · ADDED 2026-11-03 · DUE 2026-11-07 11:30Z · STATUS waiting on the gate line · SOURCE Mail 11-03 ~21:30Z, confirmed 11-04; BOARD 2026-11-04T19:52:46Z
- [ ] Mail mode-B step 15:15-15:30Z, after Mail's flip: WINDY_MAIL_EPT_AGENT=1 on the lab agent only; one mode-B read + one send with NO legacy retry; BOARD AFTER · OWNER Windy Agent · ADDED 2026-11-04 · DUE 2026-11-07 15:30Z · STATUS waiting on Mail's flip · SOURCE Mail 11-04
- [ ] ZERO DEPLOY identity part 2, pin d043705364bdea5032f38cdfbb4827ac44b3dd7b (= live + only #737), 13:00-13:20Z, each unit after 5 quiet minutes, BOARD BEFORE/AFTER. EXPECTED AFTER: Zero says its own name + "a Windy Fly agent"; the lab agent says "a Windy Fly agent" with NO instance name; normal journey OK; 0 ERROR. Rollback = the previous pin (~2 min) · OWNER Windy Agent · ADDED 2026-11-06 · DUE 2026-11-07 13:20Z · STATUS now (wake 12:50Z) · SOURCE Boss GO BY SHA BOARD 2026-11-06T23:39:48Z
- [ ] SOUL REPORT for Boss, counts only: (1) how many RUNNING agents (Zero, the lab, every hatched or roster agent, any host) load the repo default soul or the built-in fallback instead of their own (a relative soul_path resolves against the working folder); (2) one plain paragraph for Grant on what Zero's own soul adds (headings + counts, no private text). No change to any soul or path without Grant's yes · OWNER Windy Agent · ADDED 2026-11-06 · DUE 2026-11-07 18:00Z · STATUS now · SOURCE BOARD 2026-11-06T20:51:13Z (part 3 B); GRANT DECIDED item 11, 23:15:11Z

**Sun 2026-11-08**
- [ ] Tell Windy Mind the passport id (id only) of ANY lab agent of mine still running after 2026-11-09 11:00Z whose passport is not ET26-TEST-; silence = nothing to add · OWNER Windy Agent · ADDED 2026-11-04 · DUE 2026-11-08 18:00Z · STATUS later · SOURCE Mind #567
- [ ] /model reads outside_ai_label live from catalog/me once Mind's pin is live (vendored = fallback) · OWNER Windy Agent · ADDED 2026-11-04 · DUE 2026-11-08 17:00Z (Mind's pin) · STATUS waiting on Mind · SOURCE Mind pin plan 11-04

**Mon 2026-11-09**
- [ ] windy-fly-site #22 deploy (deletes the untrue Cost Control claims), 14:00-14:20Z, both domains, BOARD BEFORE/AFTER with check-claims on each · OWNER Windy Agent · ADDED 2026-11-04 · DUE 2026-11-09 14:20Z · STATUS later · SOURCE Boss BOARD 2026-11-05T17:50:11Z
- [ ] Lab #714's key rotation (WINDY_AGENT_KEY_OPS=1) on a lab agent after Eternitas deploys agent-key-ops.v1, own GO · OWNER Windy Agent · ADDED 2026-11-04 · DUE 2026-11-09 · STATUS waiting on Eternitas · SOURCE BOARD 2026-11-04T19:49:59Z item 8
- [ ] Read Boss's capture crew plan for an Agent part · OWNER Windy Agent · ADDED 2026-11-04 · DUE 2026-11-09 · STATUS later · SOURCE Boss 11-04
- [ ] Windy Talk: if Talk hasn't claimed Talk + talk-site, write their TODO.md (check the BOARD first) · OWNER Windy Agent · ADDED 2026-11-04 · DUE 2026-11-09 12:00Z · STATUS later · SOURCE Boss 11-04

**Tue 2026-11-10**
- [ ] Run Word's Windy Log vectors (incl. bad_header + injected-transcript negatives) against my decrypter; report. Build note: the Windy Log decrypter checks protected headers by an EXACT allowlist (the Vault-lease one only refuses zip/crit/apu/apv by name) · OWNER Windy Agent · ADDED 2026-11-06 · DUE 2026-11-10 · STATUS waiting on Word's vectors · SOURCE windy-contracts #304 (my windy-agent/approved at 23cb5ee5)

**Wed 2026-11-11**
- [ ] My Windy Log codons to the WINDY LOG lane (tools windy_log.*; key event ai.windy.log_key; owner-only, dark, on Chat's DM tool road; sign then seal; request_id once, 5 min; test_recorded_words_never_act). Don't build before Word's section merges · OWNER Windy Agent · ADDED 2026-11-06 · DUE 2026-11-11 18:00Z · STATUS now (draft written) · SOURCE Boss BOARD 2026-11-06T17:39:06Z, 20:42:47Z, 20:46:07Z
- [ ] The 102 bare "Windy Fly" strings in src listed by meaning (agent / product / CLI); change only the agent ones to "your Windy Fly agent" · OWNER Windy Agent · ADDED 2026-11-06 · DUE 2026-11-11 18:00Z · STATUS later · SOURCE Boss TILE RULINGS BOARD 2026-11-06T19:19:22Z
- [ ] Search mode B: the Fly flip (WINDY_SEARCH_EPT_AGENT) = its own GO + lab; drop the 502 render-failed stored-EPT send only after Search's own "live" message · OWNER Windy Agent · ADDED 2026-11-04 · DUE 2026-11-11 13:00Z (Search live) · STATUS waiting on Search · SOURCE windy-search #129; GO-DRAFTS/SEARCH-modeB-answers-20261104.md
- [ ] Desktop-door installer end-to-end lab · OWNER Windy Agent · ADDED 2026-11-04 · DUE 2026-11-11 · STATUS blocked by Hub's desktop-door code · SOURCE #726

**Fri 2026-11-13**
- [ ] ONE AGENT LIST: read the Hub's ONE dark PR (Chat-only agents added, real standing with unknown = null, agent_matrix_id) · OWNER Windy Agent · ADDED 2026-11-07 · DUE 2026-11-13 18:00Z · STATUS waiting on Hub · SOURCE Boss BATCH RULINGS BOARD 2026-11-07T00:19:02Z (8); my 00:21:20Z; Hub 00:22:10Z
- [ ] The agent-auth view of the same list: build the Fly side DARK (agent/teams.py reads it behind a flag; Chat's my-agents until then, then pairing-only) · OWNER Windy Agent · ADDED 2026-11-07 · DUE after Word's 2026-11-11 plan names the shape · STATUS waiting on Word's plan · SOURCE Hub BOARD 2026-11-07T00:22:10Z (4)
- [ ] windyfly.ai copy PR (after #22): "Windy Fly" = the app (Mobile + Desktop), the agent = "your Windy Fly agent"; own Pages GO · OWNER Windy Agent · ADDED 2026-11-06 · DUE 2026-11-13 · STATUS waiting on #22 · SOURCE Boss BOARD 2026-11-06T19:40:46Z
- [ ] hifly.ai moves to its OWN Pages project (frozen at #22's merge) before the copy deploys, own GO · OWNER Windy Agent · ADDED 2026-11-06 · DUE 2026-11-13 (plan) · STATUS waiting on #22 · SOURCE Boss BOARD 2026-11-06T19:40:46Z
- [ ] Sandbox hardening PR (Boss's list; details stay off this public file until the fix merges) · OWNER Windy Agent · ADDED 2026-11-05 · DUE 2026-11-13 · STATUS later · SOURCE Boss 11-05
- [ ] REPO REVIEW (a): docs/design/whatsapp-gateway.md from the archived private gateway repo's README, reworded, no account identifiers · OWNER Windy Agent · ADDED 2026-11-06 · DUE 2026-11-13 18:00Z · STATUS later · SOURCE Boss BOARD 2026-11-06T22:10:16Z; my 22:11:33Z
- [ ] REPO REVIEW (b): a CI check failing a PR that adds a soul file other than the default SOUL.md, or a real agent's identity text (added lines only) · OWNER Windy Agent · ADDED 2026-11-06 · DUE 2026-11-13 18:00Z · STATUS later · SOURCE Boss BOARD 2026-11-06T22:10:16Z; my 22:11:33Z
- [ ] Branch verdicts from the other owners (table below) · OWNER Grant / Kit OC1 (via Boss) · ADDED 2026-11-04 · DUE 2026-11-13 · STATUS waiting on the owners · SOURCE TODO #702

**Staged, each step on its own GO**
- [ ] Zero's own soul: (1) a PR so a relative soul_path resolves against the config file's folder first (opt-in by a config key if other agents would change); (2) Grant OKs the exact soul words (private repo); (3) the lab first, with its own small test soul; (4) Zero on Grant's OK, booked slot, way back in under 2 min · OWNER Windy Agent · ADDED 2026-11-06 · DUE none (after the soul report) · STATUS waiting on Grant · SOURCE GRANT DECIDED item 11, BOARD 2026-11-06T23:15:11Z; plan 23:17:44Z

## Waiting on

- [ ] Grant's own typed OK in this terminal before "Read TODO.md first; add every promise to it the same turn." goes into CLAUDE.md here, in windy-fly-site and in windy-0-soul · OWNER Grant · ADDED 2026-11-04 · DUE none · STATUS waiting on Grant · SOURCE asked 2026-11-04 11:45Z
- [ ] Decision on #687 (ONE-WINDY-PLAN) · OWNER Boss · ADDED 2026-11-04 · DUE none · STATUS waiting on Boss · SOURCE #687
- [ ] Revoke reason move_aborted for H13a (asked Eternitas too) · OWNER Hub · ADDED 2026-11-04 · DUE none · STATUS waiting on Hub · SOURCE windy-contracts #273
- [ ] Hub says when a lab instance takes my-windy mode B; then lab #724 there only (WINDY_MY_WINDY stays OFF everywhere until then) · OWNER Hub · ADDED 2026-11-05 · DUE none · STATUS waiting on Hub · SOURCE #724
- [ ] The exact refusal code for a revoked agent key; retired_until in the agent_retired refusal (Q9) · OWNER Eternitas · ADDED 2026-11-04 · DUE none · STATUS waiting on Eternitas · SOURCE windy-contracts #294
- [ ] "Test passport = house keys only" rule (Boss picks A blanket / B narrow) · OWNER Mind / Boss · ADDED 2026-11-04 · DUE none · STATUS waiting on Boss · SOURCE Mind 11-04
- [ ] Calendar's CK6.6 AFTER line; then an optional PR to use tools-invoke 1.7.0's starts_in_minutes + join_url · OWNER Calendar · ADDED 2026-11-05 · DUE none · STATUS waiting on Calendar · SOURCE windy-contracts #299
- [ ] Code mode B switch-on (#718, #713 merged DARK) = Code's lab + Boss GO + slot · OWNER Code · ADDED 2026-11-04 · DUE none · STATUS waiting on Code · SOURCE GO-DRAFTS/CODE-modeB-answers-20261104.md

## Later

- [ ] Zero re-pin to master (~60 commits ahead of Zero's pin): its own GO + Hub OK, one daytime slot, BOARD BEFORE/AFTER with the exact sha and what changes; rollback = the previous pin · OWNER Windy Agent · ADDED 2026-11-04 · DUE none · STATUS later · SOURCE TODO 11-04
- [ ] QA battery deploy (after #711): ONE Boss GO (lab agent update + install script + unit with InaccessiblePaths; dry run with "lab guard: ok" + one deliberate REFUSED; re-enable the timer, disabled since 2026-11-04 16:55Z) · OWNER Windy Agent · ADDED 2026-11-04 · DUE none · STATUS later · SOURCE BOARD 2026-11-04T16:55:12Z
- [ ] Purge the TRGE lab agent on the Mac lab machine (its Friday purge didn't happen): ask Hub for the next synthetic purge · OWNER Windy Agent · ADDED 2026-11-04 · DUE none · STATUS later · SOURCE TODO 11-04
- [ ] telemetry-canary is a Windy Agent emitter (Admin adds it to #895); nothing to do until Admin asks · OWNER Windy Agent · ADDED 2026-11-06 · DUE none · STATUS waiting on Admin · SOURCE Boss BOARD 2026-11-06T14:55:18Z

## Branches

Unmerged remote branches on 2026-11-04. Verdicts: mine given here; other owners' by Boss's date, Fri 11-13.

| Branch | Last commit | Owner | Verdict |
|---|---|---|---|
| archive/gauntlet/* (12 branches) | 2026-05-21 | Grant | Already archived. Keep. |
| docs/one-windy-plan | 2026-11-04 | Windy Agent | Open PR #687. Keep. |
| docs/todo-md, feat/pair-progress, fix/calendar-book-key, fix/vault-release-revocable, fix/model-switch-refused, fix/search-key-only-plain, test/no-checkout-leaks, feat/standing-latch, fix/synthetic-never-real-passport, fix/qa-battery-isolation, fix/calendar-key-reused | 2026-11-04 | Windy Agent | PRs #702-#712 all MERGED (11-04/11-05). Delete only by Boss's line. |
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

Local-only branches: none left open (fix/home-pair-followups = #720, feat/home-install-desktop = #726, fix/home-install-finish
= #728, feat/credentials-bak-prune = #725, fix/standing-followups = #730, fix/typing-stop-keeps-cancel = #734, all merged).

## Done

- [x] TODO catch-ups merged (Boss 23:57:40Z) · OWNER Windy Agent · ADDED 2026-11-07 · DONE 2026-11-07 · PROOF #738 66a6d4d0, #739 2859e23b, windy-fly-site #25 a5717c21 + #26 39bc0582, windy-0-soul #3 34376523 (+ #2 23714607)
- [x] Second reads stamped (windy-agent/approved): windy-traveler #18 + #19 (offline-language spec, checked against production models.json, the pinned en-pt files and windy-pro #1714), windytraveler-site #26, windy-fly-desktop #3 + #4 + #6, windy-contracts #304 · OWNER Windy Agent · ADDED 2026-11-07 · DONE 2026-11-07 · PROOF windy-agent/approved read back on each head
- [x] AGENTS on the dashboard: answered 22:33:02Z (then: Chat onboarding my-agents); superseded by the ONE AGENT LIST ruling (Hub GET /api/v1/agents), the build part is the agent-auth view line in Fri 2026-11-13 · OWNER Windy Agent · ADDED 2026-11-06 · DONE 2026-11-07 · PROOF BOARD 2026-11-06T22:33:02Z; Boss 2026-11-07T00:19:02Z (8)
- [x] ONE AGENT LIST answered on the BOARD (agree, 3 Hub-list fixes + the agent-auth view) · OWNER Windy Agent · ADDED 2026-11-07 · DONE 2026-11-07 · PROOF BOARD 2026-11-07T00:21:20Z
- 2026-11-07: windy-0-soul #2 RECOVERY.md MERGED 23714607 00:06Z (both stamps were on 2f2fb35b since 11-04; this file's
  'waiting on Boss' line was stale, Hub caught it).
- 2026-11-06: #737 MERGED 6260343c (default + fallback soul say "a Windy Fly agent"; the instance name comes from
  [agent] name in config, none = no instance sentence). #736 MERGED 63d349da (the IDENTITY line: a Windy Fly agent with
  its own name). #735 77b76a62 + #729 8335ce1f (TODO). Repo description set (Boss 18:57:55Z).
- 2026-11-06: Zero identity deploy 20:40-20:47Z (Boss GO 20:24:47Z): 3 units on the new pin, 0 ERROR; AFTER honest on the
  BOARD 20:47:45Z (the answer was still "Windy Fly": the default soul + a hard-coded name -> #737).
- 2026-11-06: Windy Mind's synthetic-lane 402 (gemini in the rotation) fixed on Kit 0 23:21Z; my re-run JOURNEY-OK, the lab
  owner turn served, 0 x 402.
- 2026-11-06: BOARD answers: TILE lines (18:57:06Z, update 19:21:43Z), REPO REVIEW (22:11:33Z), Twilio master token =
  AGENT NO (22:16:35Z), AGENTS list source (22:33:02Z), item 11 staged plan (23:17:44Z). CFO inventory answered.
  Windy Memory/Windy Log codon drafts (crypto + road settled with Word, Hub, Mobile).
- 2026-11-05: MERGED #709 b0c98dd0 (a refused mint stops the agent), #711 e17884b7 (lab guard), #712 (Calendar 422 words),
  #714 d670d59b (key rotation, dark), #716 0fcc1e75 (Search mode B, dark), #718 bf950072 (Code bus mode B, dark), #719
  acc24cd3 (consent surface final), #720 e054b5ec, #721 399ee555, #722 c7c6989e, #723 34a58e44, #724 a9863c42 (my-windy,
  dark), #725 (backup prune), #726 52a9fdd9 (desktop-door installer, dark), #727 1b2ba9cb (https-only hub_url), #728
  d3ca1574 (home install follow-ups), #730 (standing follow-ups), #731 2ed6b8ca (wipe the plaintext memory copy after a
  verified import), #732, #733, #734 (typing sign).
- 2026-11-05: CL5a PASS (owner-call flag on); Code mode-B lab for #713 PASS; Connect lab #1 legs 1, 2, 5 PASS (leg 3 a real
  gap, leg 4 later), lab passports purged; Zero on claude-sonnet-5-5 (Grant's decision) with the journey green again;
  Zero's slow replies fixed; sandbox drop-ins on the agent service and the lab.

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
