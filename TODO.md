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

Last full rewrite: 2026-11-07 00:01Z (catch-up, Boss BOARD 2026-11-06T23:57:40Z); updated 2026-11-07 00:19Z.

## Now

**Sat 11-07**
- 09:00Z Search #124 (EPT+agent on the bearer path): no code change (we send the typ EPT). Run one journey after
  Search says it's live.
- Mail API AFTER lab (Mail asked 11-03; mine). GATE ~10:45Z: no step naming the lab owner until a same-hour BOARD line
  (LIVE + test-tagged + durable) from Hub or Windy Fly Mobile. Windy Fly Mobile's phone hatches ~11:00-11:10Z (the
  hatch is a consent surface: no lane ever signs in or types as the owner). Then a FRESH lab agent (lab guard,
  WINDY_MAIL_EPT_AGENT unset, delete only what this run created). From ~11:20Z on Mail's cue: (1) one legacy-EPT read;
  (2) the same send twice with the same key = 1 arrival + Idempotent-Replayed; (3) one birth-announcement send; (4) one
  send with its own body_html; (5) one text-only send. 15:15-15:30Z, after Mail's flip: WINDY_MAIL_EPT_AGENT=1 on the lab
  agent only, one mode-B read + one send with NO legacy retry. BOARD BEFORE/AFTER.
- 13:00-13:20Z ZERO DEPLOY, identity part 2 (Boss GO BY SHA d043705364bdea5032f38cdfbb4827ac44b3dd7b, BOARD
  2026-11-06T23:39:48Z) = the live pin + only #737. Each unit after 5 quiet minutes; BOARD BEFORE/AFTER. EXPECTED AFTER:
  Zero answers "who are you?" with its own name + "a Windy Fly agent"; the lab agent says "a Windy Fly agent" with NO
  instance name (it has none of its own); normal journey OK; 0 ERROR. Rollback = the previous pin (~2 min).
- BY 18:00Z, SOUL REPORT for Boss (BOARD 2026-11-06T20:51:13Z part 3 B; GRANT DECIDED item 11, 23:15:11Z), counts only:
  (1) how many RUNNING agents (Zero, the lab, every hatched or roster agent, any host) load the repo default soul or the
  built-in fallback instead of their own (a relative soul_path resolves against the working folder); (2) one plain
  paragraph for Grant: what Zero's own soul adds (headings + counts, no private text). No change to any soul or path
  without Grant's yes.
- BY 18:00Z: catch-up TODO PRs (Boss 23:57:40Z): windy-agent #738 MERGED 66a6d4d0 and windy-fly-site #25 MERGED a5717c21
  (00:18Z); windy-0-soul #3 re-read after merging main (it conflicted with #2).

**Sun 11-08**
- By 18:00Z: tell Windy Mind the passport id (id only) of ANY lab agent of mine still running after Mon 11-09 11:00Z
  whose passport is not ET26-TEST- (Mind #567). Silence = nothing to add.
- /model: read `outside_ai_label` live from catalog/me once Mind's pin is live (17:00Z); vendored = fallback.

**Mon 11-09**
- windy-fly-site #22 (deletes the untrue Cost Control claims) deploys 14:00-14:20Z on its own GO, both domains.
- Eternitas deploys agent-key-ops.v1: then lab #714's rotation (WINDY_AGENT_KEY_OPS=1) on a lab agent, own GO.
- Capture crew plan (Boss): may name an Agent part; read it that day.
- Windy Talk: if Talk hasn't claimed Talk + talk-site, I write their TODO.md by 12:00Z (check the BOARD first).

**Tue 11-10**
- Windy Log tool contract (log-tools.v1, was memory-tools.v1; Windy Fly Mobile drafts, Word makes the vectors): run the
  draft's test vectors against my decrypter; report.

**Wed 11-11**
- By 18:00Z: my Windy Log codons to the WINDY LOG lane (Grant named it, BOARD 2026-11-06T20:42:47Z; tools windy_log.*,
  was windy_memory.*, BOARD 20:46:07Z). The 11-06 rulings stand: owner-only, dark, on Chat's DM tool road; each result
  signed by the owner's computer, then sealed to the agent's memory key (the Vault-lease JWE profile); request_id
  once, 5 min; named test test_recorded_words_never_act. DON'T BUILD before Word's section merges.
- By 18:00Z: the 102 bare "Windy Fly" strings in src listed by meaning (agent / product / CLI); change only the agent ones
  to "your Windy Fly agent" (Boss TILE RULINGS 2026-11-06T19:19:22Z: the "Windy Fly agent" strings are right and stay).
- Search mode B live 13:00Z; the Fly flip (WINDY_SEARCH_EPT_AGENT) = its own GO + lab. Before it: drop the 502
  render-failed stored-EPT send only after Search's own "live" message.
- Desktop-door installer end-to-end lab (constraint: Hub's desktop-door code).

**Fri 11-13**
- windyfly.ai copy PR (plan APPROVED, Boss BOARD 2026-11-06T19:40:46Z): after #22; "Windy Fly" = the app (Mobile +
  Desktop), the agent = "your Windy Fly agent". hifly.ai keeps today's page: it moves to its OWN Pages project first
  (frozen at #22's merge), each on its own GO. "Free during early access" changes the same day the $1 hatch goes on.
- Sandbox hardening PR (Boss's list, 11-05; details stay off this public file until the fix merges).
- REPO REVIEW (Boss BOARD 2026-11-06T22:10:16Z; answered 22:11:33Z): (a) a docs PR: docs/design/whatsapp-gateway.md
  from the archived private gateway repo's README, reworded, with no account identifiers; (b) a CI check that fails a
  PR adding a soul file other than the default SOUL.md, or a real agent's identity text (added lines only). Hub reads;
  Boss stamps.
- Branch verdicts from the other owners (table below), by Boss's date.

**Staged, each step on its own GO (GRANT DECIDED item 11, BOARD 2026-11-06T23:15:11Z; plan posted 23:17:44Z)**
- Zero's own soul: (0) Sat's soul report; (1) a PR so a relative soul_path resolves against the config file's folder
  first (opt-in by a config key if other agents would change); (2) Grant OKs the exact soul words (private repo);
  (3) the lab first, with its own small test soul; (4) Zero on Grant's OK, booked slot, way back in under 2 min.

## Waiting on

- **Grant:** his own typed OK in this terminal before the line "Read TODO.md first; add every promise to it the same
  turn." goes into CLAUDE.md in windy-agent, windy-fly-site and windy-0-soul. Asked 11-04 11:45Z.
- **Grant:** the staged OK for Zero's own soul (above).
- **Boss:** decision on #687 (ONE-WINDY-PLAN).
- **Hub:** revoke reason `move_aborted` for H13a (asked Eternitas too); says when a lab instance takes my-windy mode B
  (then lab #724 there only; WINDY_MY_WINDY stays OFF everywhere until then).
- **Eternitas:** the exact refusal code for a revoked agent key; `retired_until` in the agent_retired refusal (Q9).
- **Mind:** "test passport = house keys only" rule (Boss picks A blanket / B narrow).
- **Calendar:** its CK6.6 AFTER line; then an optional PR to use tools-invoke 1.7.0's starts_in_minutes + join_url.
- **Code:** IDE agent-bus + windycode_web mode B are merged DARK (#718, #713); switch-on = Code's lab + Boss GO + slot.

## Later

- Zero re-pin to master (master is ~60 commits ahead of Zero's pin): its own GO + Hub OK, one daytime slot, BOARD
  BEFORE/AFTER with the exact sha and the list of what changes; rollback = the previous pin.
- QA battery deploy (after #711, merged): ONE Boss GO (lab agent update + install script + unit with InaccessiblePaths;
  dry run pasting "lab guard: ok" + one deliberate REFUSED; re-enable the timer, DISABLED since 11-04 16:55Z).
- Purge the TRGE lab agent on the Mac lab machine: it was due at a Friday passport purge that didn't happen; ask Hub
  for the next synthetic purge and name it there.
- telemetry-canary is a Windy Agent emitter (Boss BOARD 2026-11-06T14:55:18Z; Admin adds it to #895). Nothing to do
  until Admin asks.
- AGENTS on the dashboard (Boss 2026-11-06T22:31:24Z): answered 22:33:02Z (today's one source = Windy Chat onboarding
  my-agents; status is not in it). Build what Hub + Word design asks of the agent.

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
