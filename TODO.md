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
- [ ] Zero re-pin to master (67 commits ahead of its pin): BOOKED SUN 11-08 15:30-16:00Z on Windy 0, GO by sha at the BEFORE; #746-#755 merge first; BEFORE with Hub's OK by 14:00Z incl. Eternitas standing and each unit's env-file lines; rollback = the previous pin in under 2 min · OWNER Windy Agent · ADDED 2026-11-04 · DUE 2026-11-08 16:00Z · STATUS waiting on the merges · SOURCE Boss BOARD 2026-11-07T16:26:51Z; 19:26:03Z (the env-file check)
- [ ] #757: no OWNER band with no owner in the turn (background goals run lower and pause to ask the owner; the phone-result turn; /benchmark) · OWNER Windy Agent · ADDED 2026-11-07 · DUE 2026-11-10 18:00Z · STATUS waiting on CI (Hub + Boss stamped 682da682) · SOURCE Boss BOARD 2026-11-07T19:26:03Z
- [ ] Follow-up to #757 (Boss's second ruling on background goals; details in its PR) with a test and a mutant, Hub read, Boss stamp · OWNER Windy Agent · ADDED 2026-11-07 · DUE 2026-11-09 18:00Z · STATUS blocked by #757's merge · SOURCE Boss BOARD 2026-11-07T19:39:14Z
- [ ] Follow-up to #755 (Hub's read note; details stay off this public file until it merges) · OWNER Windy Agent · ADDED 2026-11-07 · DUE 2026-11-13 · STATUS blocked by #755's merge · SOURCE Hub's #755 stamp note 2026-11-07
- [ ] Calendar's lab Tue 11-10 13:05Z: the Chat roster agent runs from master; my pre-lab line names its exact sha · OWNER Windy Agent · ADDED 2026-11-07 · DUE 2026-11-10 13:05Z · STATUS later · SOURCE Windy Calendar 2026-11-07

Line standard (Boss BOARD 2026-11-07T00:02:22Z): - [ ] what · OWNER · ADDED · DUE · STATUS · SOURCE; Done = - [x] · DONE · PROOF.

**Sat 2026-11-07**

- [ ] Mind: lab (synthetic) tool turns fail on the 2nd /v1/chat (the synthetic chain: nemotron retired, @cf llama skips big prompts, gpt-oss:20b answers with its reasoning unsplit, so 503). Real callers and Zero unaffected. Hold lab tool turns; re-run the one lab search turn when Mind's fix (windy-mind #594, dark; I read it: the tail model must take an ~11k-token turn) is live · OWNER Mind (fix), Windy Agent (re-run) · ADDED 2026-11-07 · DUE none · STATUS blocked by windy-mind #594's switch-on GO (Mind) · SOURCE BOARD 2026-11-07 ~09:1xZ (Agent); Mind's answer ~09:1xZ
- [ ] Zero's owner-path identity check: Grant asks Zero "who are you?" himself (an owner turn can't be scripted; a non-owner probe never hears the instance name, by design) · OWNER Grant · ADDED 2026-11-07 · DUE none · STATUS waiting on Grant · SOURCE Boss BOARD 2026-11-07T13:09:43Z (b)


- [ ] #750: the agent's own labels say "Windy Fly Agent" (reply signature header, Matrix presence, the hatch passport description); after #749 merges, merge master in (no force-push) and move the strings doc's group 2 to changed · OWNER Windy Agent · ADDED 2026-11-07 · DUE 2026-11-07 · STATUS waiting on #749's merge · SOURCE Boss BOARD 2026-11-07T15:55:25Z
- [ ] After #748: the soul rule follows the file itself (device + inode), so a hard link to the soul is closed too · OWNER Windy Agent · ADDED 2026-11-07 · DUE 2026-11-10 · STATUS waiting on CI (#756, Hub + Boss stamped 4b75a34b) · SOURCE Hub's #748 stamp note 2026-11-07; Boss BOARD 2026-11-07T15:55:25Z
- [ ] DNA_STRAND_MASTER_PLAN.md for windy-agent v1.0: genes + codons, the Windy Log codons AG-1..AG-8 as their own gene; TODO lines carry the AG ids the day each starts · OWNER Windy Agent · ADDED 2026-11-07 · DUE 2026-11-13 18:00Z · STATUS later (v0.1 merged #752) · SOURCE Windy Log 2026-11-07 (codons recorded FINAL, VISION section 8 net 4); ADDENDUM-5 (the strand shape)

**Sun 2026-11-08**
- [ ] Tell Windy Mind the passport id (id only) of ANY lab agent of mine still running after 2026-11-09 11:00Z whose passport is not ET26-TEST-; silence = nothing to add · OWNER Windy Agent · ADDED 2026-11-04 · DUE 2026-11-08 18:00Z · STATUS later · SOURCE Mind #567
- [ ] /model reads outside_ai_label live from catalog/me once Mind's pin is live (vendored = fallback) · OWNER Windy Agent · ADDED 2026-11-04 · DUE 2026-11-08 17:00Z (Mind's pin) · STATUS waiting on Mind · SOURCE Mind pin plan 11-04

**Mon 2026-11-09**
- [ ] windy-fly-site #22 deploy (deletes the untrue Cost Control claims) with #29 riding along (IndexNow key + the More from Windy links), 14:00-14:20Z, both domains, GO by sha at the BEFORE, BOARD BEFORE/AFTER with check-claims on each · OWNER Windy Agent · ADDED 2026-11-04 · DUE 2026-11-09 14:20Z · STATUS later (#29 Hub + Boss stamped f26b5d87; merging it deploys it, so it merges in the slot) · SOURCE Boss BOARD 2026-11-05T17:50:11Z; 2026-11-07T15:55:25Z
- [ ] Lab #714's key rotation (WINDY_AGENT_KEY_OPS=1) on a lab agent after Eternitas deploys agent-key-ops.v1, own GO · OWNER Windy Agent · ADDED 2026-11-04 · DUE 2026-11-09 · STATUS waiting on Eternitas · SOURCE BOARD 2026-11-04T19:49:59Z item 8

**Tue 2026-11-10**
- [ ] When I build the Windy Log decrypter: the exact header allowlist in front of the decrypt (the vectors prove it: without it zip/apu come out decrypt_failed, not bad_header), and test_recorded_words_never_act on positive 4 · OWNER Windy Agent · ADDED 2026-11-07 · DUE none · STATUS blocked by Word's section merging · SOURCE windy-contracts #304 at 8d331073


**Wed 2026-11-11**
- [ ] The 102 bare "Windy Fly" strings in src listed by meaning (agent / product / CLI); change only the agent ones to "your Windy Fly agent" · OWNER Windy Agent · ADDED 2026-11-06 · DUE 2026-11-11 18:00Z · STATUS waiting on CI (#749, Hub + Boss stamped; 97 found today, sorted in docs/brand/windy-fly-strings.md) · SOURCE Boss TILE RULINGS BOARD 2026-11-06T19:19:22Z
- [ ] Search mode B: the Fly flip (WINDY_SEARCH_EPT_AGENT) = its own GO + lab; drop the 502 render-failed stored-EPT send only after Search's own "live" message · OWNER Windy Agent · ADDED 2026-11-04 · DUE 2026-11-11 13:00Z (Search live) · STATUS waiting on Search · SOURCE windy-search #129; GO-DRAFTS/SEARCH-modeB-answers-20261104.md
- [ ] Desktop-door installer end-to-end lab · OWNER Windy Agent · ADDED 2026-11-04 · DUE 2026-11-11 · STATUS blocked by Hub's desktop-door code · SOURCE #726

**Fri 2026-11-13**
- [ ] ONE AGENT LIST: read the Hub's ONE dark PR (Chat-only agents added, real standing with unknown = null, agent_matrix_id) · OWNER Windy Agent · ADDED 2026-11-07 · DUE 2026-11-13 18:00Z · STATUS waiting on Hub · SOURCE Boss BATCH RULINGS BOARD 2026-11-07T00:19:02Z (8); my 00:21:20Z; Hub 00:22:10Z
- [ ] The agent-auth view of the same list: build the Fly side DARK (agent/teams.py reads it behind a flag; Chat's my-agents until then, then pairing-only) · OWNER Windy Agent · ADDED 2026-11-07 · DUE none · STATUS blocked by Word's 2026-11-11 plan naming the shape · SOURCE Hub BOARD 2026-11-07T00:22:10Z (4)
- [ ] windyfly.ai copy PR (after #22): "Windy Fly" = the app (Mobile + Desktop), the agent = "your Windy Fly agent"; own Pages GO · OWNER Windy Agent · ADDED 2026-11-06 · DUE 2026-11-13 · STATUS waiting on #22 · SOURCE Boss BOARD 2026-11-06T19:40:46Z
- [ ] hifly.ai moves to its OWN Pages project (frozen at #22's merge) before the copy deploys, own GO · OWNER Windy Agent · ADDED 2026-11-06 · DUE 2026-11-13 (plan) · STATUS waiting on #22 · SOURCE Boss BOARD 2026-11-06T19:40:46Z
- [ ] Hardening (Boss's list), three PRs: #753 (stop by exact PID only), #754 (each agent reads only its own env file), #755 (the shell sandbox; details stay off this public file until it merges) · OWNER Windy Agent · ADDED 2026-11-05 · DUE 2026-11-13 · STATUS waiting on CI (all three Hub + Boss stamped after Hub's review changes) · SOURCE Boss 11-05; option B for #754 Boss BOARD 2026-11-07T16:13:24Z
- [ ] REPO REVIEW (b): a CI check failing a PR that adds a soul file other than the default SOUL.md, or a real agent's identity text (added lines only) · OWNER Windy Agent · ADDED 2026-11-06 · DUE 2026-11-13 18:00Z · STATUS waiting on CI (#747, Hub + Boss stamped 1384e706 after Hub's review changes) · SOURCE Boss BOARD 2026-11-06T22:10:16Z; my 22:11:33Z
- [ ] Branch verdicts from the other owners (table below) · OWNER Grant / Kit OC1 (via Boss) · ADDED 2026-11-04 · DUE 2026-11-13 · STATUS waiting on the owners · SOURCE TODO #702

**Staged, each step on its own GO**
- [ ] Zero's own soul, each step on its own GO: (1) #744 MERGED a2f60cbc; (2) #748 MERGED 0ba67532; (3) windy-0-soul #6, a DRAFT marking its two host/operator sections owner-only = the exact words for Grant, Hub + Boss read it first (Boss's word changes of 2026-11-07T16:02:04Z go in first); (4) the soul_path fix (resolve against the config file's folder), which switches Zero's own soul ON, only on Grant's OK, booked slot, way back in under 2 min · OWNER Windy Agent · ADDED 2026-11-06 · DUE none (Grant's OK) · STATUS waiting on Hub's read of #6 (Boss's word changes are in), then Grant · SOURCE GRANT DECIDED item 11, BOARD 2026-11-06T23:15:11Z; Boss 2026-11-07T14:31:58Z (1), 16:02:04Z (1)

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

- [ ] QA battery deploy (after #711): ONE Boss GO (lab agent update + install script + unit with InaccessiblePaths; dry run with "lab guard: ok" + one deliberate REFUSED; re-enable the timer, disabled since 2026-11-04 16:55Z) · OWNER Windy Agent · ADDED 2026-11-04 · DUE none · STATUS later · SOURCE BOARD 2026-11-04T16:55:12Z
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

- [x] #748: the file tools refuse ANY soul file the agent has loaded, below the owner, whatever its name · OWNER Windy Agent · ADDED 2026-11-07 · DONE 2026-11-07 · PROOF 0ba67532 (Hub + Boss stamps at c13e3347, CI green)
- [x] Grant's order: TODO up to date by 16:05Z (#745), knock-outs 16:05-17:15Z, a gap analysis page per repo I own (windy-agent, windy-fly-site, windy-0-soul), one BOARD line · OWNER Windy Agent · ADDED 2026-11-07 · DONE 2026-11-07 · PROOF #745 merged 0a800d25; pages published 16:25Z (marked draft 16:29Z per Boss 16:27:28Z); BOARD 2026-11-07T16:27:45Z
- [x] Read Boss's capture crew plan for an Agent part · OWNER Windy Agent · ADDED 2026-11-04 · DONE 2026-11-07 · PROOF read 2026-11-07: no Agent part; one line about starting training flagged to Windy Clone, which is fixing it in its own PR
- [x] Windy Talk's TODO files: Talk owns them · OWNER Windy Agent · ADDED 2026-11-04 · DONE 2026-11-07 · PROOF windytalk and windytalk-site each carry Talk's own TODO.md (checked 2026-11-07)
- [x] REPO REVIEW (a): docs/design/whatsapp-gateway.md from the archived gateway README, reworded, no account identifiers · OWNER Windy Agent · ADDED 2026-11-06 · DONE 2026-11-07 · PROOF #746 merged 03d56601 (Hub + Boss stamps, CI green)
- [x] DNA_STRAND_MASTER_PLAN.md v0.1 (sections 0-4 + 8, 14 of Grant's quotes checked word for word) · OWNER Windy Agent · ADDED 2026-11-07 · DONE 2026-11-07 · PROOF #752 merged 28990b86
- [x] #744 soul owner-only sections: the exact OWNER band only; the file tools and github.fetch_file refuse a SOUL.md below the owner; fails closed · OWNER Windy Agent · ADDED 2026-11-07 · DONE 2026-11-07 · PROOF a2f60cbc (Hub + Boss stamps at 13dd9efb, CI green)
- [x] My Windy Log codons (AG-1..AG-8) delivered to the Windy Log lane, which recorded them FINAL · OWNER Windy Agent · ADDED 2026-11-06 · DONE 2026-11-07 · PROOF frozen record sha256/16 dc4fbcdedbf414a2; Windy Log's reply 2026-11-07 ~15:55Z
- [x] #745 (TODO, Sat afternoon) merged · OWNER Windy Agent · ADDED 2026-11-07 · DONE 2026-11-07 · PROOF 0a800d25
- [x] Calendar asked where #685 stands: merged 2026-11-02; the book keys are deterministic and tools-invoke 1.5.0's 422 is handled; answered · OWNER Windy Agent · ADDED 2026-11-07 · DONE 2026-11-07 · PROOF 1a96a227 (#685)
- [x] Mail mode-B step: one process with WINDY_MAIL_EPT_AGENT=1 on the lab agent; mailbox read 200 + one send 200, each with the EPT+agent + a DPoP proof header, 1 HTTP call each, 0 legacy retries · OWNER Windy Agent · ADDED 2026-11-04 · DONE 2026-11-07 · PROOF BOARD AFTER 2026-11-07 ~15:16Z; Mail's flag on 15:15:09Z
- [x] SOUL REPORT to Boss: 61 of 61 running agents run a generic default or the built-in fallback (Zero: the default via a relative soul_path; two lab agents: the fallback; 58 Chat roster agents: one generic prompt); only Zero has its own soul, and it is off; plain paragraph for Grant + a caution on two sections · OWNER Windy Agent · ADDED 2026-11-06 · DONE 2026-11-07 · PROOF BOARD 2026-11-07T14:31:04Z + correction ~14:32Z; Boss accepted 14:31:58Z
- [x] TRGE lab agent (11-03 wake lab, later on a lab Mac): it WAS still running on the fallback soul; stopped (exact PID), passport revoked by Eternitas, state kept · OWNER Windy Agent · ADDED 2026-11-04 · DONE 2026-11-07 · PROOF Boss GO 2026-11-07T14:34:22Z; Eternitas AFTER 14:39:14Z
- [x] #743 (Sat results) merged · OWNER Windy Agent · ADDED 2026-11-07 · DONE 2026-11-07 · PROOF b0d580a8
- [x] Search #124 AFTER (Agent side): live 09:01:18Z; journeys OK; a lab owner-path search = /v1/search 200 twice; 0 wrong_token_type / 401 · OWNER Windy Agent · ADDED 2026-11-06 · DONE 2026-11-07 · PROOF BOARD 2026-11-07 ~09:1xZ (Agent), Search's AFTER 09:02:13Z
- [x] Zero identity part 2 deployed (d043705, 13:00-13:09Z): health OK, 0 ERROR, journey OK. The non-owner probe can't hear the instance name (by design; the GO's expectation was wrong, Boss 13:09:43Z). No rollback (the old pin had the hard-coded name) · OWNER Windy Agent · ADDED 2026-11-06 · DONE 2026-11-07 · PROOF BOARD AFTER 2026-11-07 ~13:09Z; Boss 13:09:43Z
- [x] zero-lab identity: its old DM session repeated its own 11-06 answer (from #736's hard-coded name); one journal entry marked superseded (not deleted); in a FRESH lab room it answers "I am a Windy Fly agent", no instance name, as d043705 intends · OWNER Windy Agent · ADDED 2026-11-07 · DONE 2026-11-07 · PROOF Boss GO 13:09:43Z (a) + 14:22:49Z; BOARD AFTERs ~14:20Z + ~14:24Z
- [x] Mail API AFTER lab, part 2: Hub API-hatched labmail9bffe9 (ET26-TEST-R5FY, no consent step), brought home in a sandboxed lab, the hand-off token shredded after bring-home; legacy read 200/200; MA2 same key twice = 1 arrival + replay; MA3 birth announcement; MA4 html (a 429 per-minute limit first, correct, never sent); MA5 text-only; all PASS. The empty inbox is by design (the Hub sends the owner's hatch mail since Mail #154; the 11-04 plan line expecting a welcome mail was wrong) · OWNER Windy Agent · ADDED 2026-11-03 · DONE 2026-11-07 · PROOF Mail AFTER part 2 BOARD 2026-11-07 ~13:30Z; my AFTER ~13:3xZ; Boss rulings 10:46:18Z, 10:48:48Z, 10:49:20Z
- [x] Name matchers normalise Unicode spaces first (NFKC; U+202F / U+00A0 -> a plain space): the lane's identity check and journey check · OWNER Windy Agent · ADDED 2026-11-07 · DONE 2026-11-07 · PROOF Boss BOARD 2026-11-07T14:22:49Z; journey OK after the change 14:24:33Z
- [x] #741 (Windy Log vectors + the issued_at gap) merged · OWNER Windy Agent · ADDED 2026-11-07 · DONE 2026-11-07 · PROOF 72853997 (both stamps + CI at 0cec59fe)
- [x] Vectors v3 (the 16th case: stale with a FRESH issued_at) re-run: 16/16 as expected, all 4 mutants caught (age from issued_at now fails); windy-agent/approved + a PR comment at #304's head · OWNER Windy Agent (asked), Word + Windy Fly Mobile (built) · ADDED 2026-11-07 · DONE 2026-11-07 · PROOF windy-contracts #304 at 10e8c46a (file sha256/16 e6f452ca35f9c245), status 02:54:49Z
- [x] Windy Log vectors run EARLY (due 2026-11-10): Word's v2 (15: 4 open, 11 drop, why list with decrypt_failed) through master's vault/lease_jwe.py + an L6 (a)-(g) checker, 15/15 as expected; the ai.windy.log_key statement verifies; 3 of 4 mutants caught, the 4th = an issued_at gap, closed in v3 (line above) · OWNER Windy Agent · ADDED 2026-11-06 · DONE 2026-11-07 · PROOF windy-contracts #304 at 8d331073 (file sha256/16 aef4ac9fdb015a2f); report to Word + Mobile 2026-11-07 ~02:4xZ
- [x] bad_tag + other_key merged into ONE decrypt_failed (both a drop; other_key would also catch a tampered encrypted_key) · OWNER Windy Agent + Word · ADDED 2026-11-07 · DONE 2026-11-07 · PROOF windy-contracts #304 at 8d331073
- [x] #740 (same-day lines + the line standard) merged · OWNER Windy Agent · ADDED 2026-11-07 · DONE 2026-11-07 · PROOF 5419c3be (both stamps + CI at 1ea6baf3)
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
