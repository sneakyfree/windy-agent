# One Windy: one sign-up, one birth canal, one agent (plan for Grant)

**Status:** DRAFT, but section E's recommendations were APPROVED by Grant on 2026-11-02 15:1xZ ("I concur with all of your recommendations... Go.", BOARD 15:16:05Z). Co-authors' facts still due Tue 2026-11-03 18:00Z; Boss assigns the work.
**Lead:** Windy Agent. **Co-authors:** Windy Chat, Windy Hub, Windy Word, Windy Mind, Eternitas. Each owner fills in its own rows, marked `[Owner: ...]`.
**Rule for everything here (Boss, 2026-11-02 15:01Z):** nothing moves a real person's agent before a lab proof and Boss's GO.

## 0. Grant's direction (2026-11-02, given in the Windy Agent terminal, in substance)
1. **One account-creation app** for the whole ecosystem: the Windy Word sign-up, already super-developed and stress-tested. Whatever platform a person finds first, they create their account in that same app. They can use Google, Apple or GitHub, and from then on they have Windy logins to activate any platform on the dashboard. It changes in one place, never 20.
2. **One birth canal, many doorways:** the Windy Fly mobile app, desktop, windyfly.ai and Windy Chat all enter the same canal. Inside it the person must activate Windy Mind and add a source of compute (Anthropic, OpenAI, ...), quickly and with a thumb. Eternitas is the only certifier, at the end.
3. **One agent:** the same Windy Fly agent and the same experience wherever it is met (Windy Chat desktop or mobile, the Fly app). It should feel like a Claude Code terminal. Stay out of the model's way and keep the tools simple: models outgrow skills every week. The personality sliders stay; raw/unfiltered is the default.
4. **Never born clueless:** the agent knows the Windy platforms it has and what it is entitled to there, and says only what is true.

## A. Sign-up audit: every platform's sign-up / login surface
Goal: every surface sends the person to the one Windy Accounts app (SSO with Windy, Google, Apple and GitHub logins). Each owner fills in its row.

| Platform | Surface (web / app / CLI) and URL or screen | Today's behaviour | Redirects to Windy Accounts? | Change needed | Owner |
|---|---|---|---|---|---|
| Windy Accounts (THE one app) | **account.windyword.ai**: OAuth doorway GET /api/v1/oauth/authorize + /oauth/signup (routes/oauth.ts:419,692), with HQ doorway counts | the one account app | n/a (it is the app) | none | Windy Hub |
| Windy Word web app | **app.windyword.ai/auth** (web SPA Auth.jsx) | its OWN form: POSTs to /api/v1/auth/register and /login (Auth.jsx:179,197); its social buttons hit /auth/oauth/<p>/start with no return_to | **NO** | retire the form and redirect to account.windyword.ai: Hub's row **H2** below (M, PR by Fri 11-06, ships at the next web + account-server deploy after its GO) | Windy Hub / Word |
| Windy Word website (W1-W3) | windyword.ai Download, Navbar "Sign in", Offering buttons; the `?onelogin=0` opt-out; the "Confirm email" modal | Main already goes to Windy Accounts by default (#284; OIDC code+PKCE, client windyword-site). The LIVE site is still the a4cd7714 build with its OWN AuthModal form | YES in code, NO live yet | DEPLOY Thu 11-05 (after Hub's Turnstile enforce flip Wed 11-04); delete the opt-out + AuthModal sign-in/sign-up + the confirm modal by Thu 11-12 (S) | Windy Word |
| Windy Word desktop app (W4-W5, W10-W11) | setup wizard account step (installer-v2 account-step.js) + Settings "Sign in / Create account" + Cloud sign-in | The app's OWN email+password form (POST /auth/register, /auth/login); its Google/GitHub/Apple buttons already go to Windy Accounts (loopback PKCE) | **NO** (the form) | The SAME Windy Accounts page in an in-app window, finishing on the loopback listener; an offline "No connection, try again" state; a guard test banning /auth/login and /auth/register in the desktop. **M**, in **1.9.66** (RC Tue 11-10, publish Thu 11-12); Hub reads it | Windy Word |
| Windy Word desktop windows (W6-W9) | Profile & billing; Chat, Mail, Calendar windows; desktop hatch | Profile = Hub's settings page (live). Chat/Mail/Calendar = each one's own "Sign in with Windy" (silent once SI10 is live: desktop half Mon 11-09 at the earliest, needs Hub's SI10 server half). Desktop hatch = Hub's hatch page in a signed-in window (D8) | YES | SI10 (S-M) | Windy Word |

Full rows W1-W11 with proofs: Windy Word's message to Windy Agent, 11-02 17:15Z (code on main, not a device run).

**The "my computer" home = the Windy Word desktop app (Word, 11-02 17:24Z): YES, as a MANAGED install.**
- **What it does:**
  - one click in Windy Word's Fly window installs a pinned uv + a Python 3.12 venv + a pinned windyfly in a per-user folder (not bundled; Word's bundled Python is 3.11.9);
  - the computer makes the agent's key (it never leaves the 0600 credentials file);
  - Word shows the QR + fallback code, drawn locally;
  - the agent runs as its OWN per-user OS service through windyfly's supervisor backends (launchd / systemd user / scheduled task), so it outlives the app;
  - an On/Off, "Stop and remove" panel.
- **Size and date:** L for Word (~6-8 days + every-OS lab), behind HOST_AGENT_LOCAL. Earliest **1.9.67** (build 11-16..11-20, lab 11-23, RC 11-24), IF Agent's and Hub's pieces land by Fri 11-13.
- **What breaks, and the fixes:**
  1. auto_backup is ON by default in windyfly; a local home should write it OFF unless the person turns it on [Boss decides];
  2. **SLEEP = LOST MESSAGES today** (Agent, from code): the matrix channel keeps no sync position (no nio store_path) and drops messages older than 120 s, so the owner's messages from while the laptop slept are silently skipped. **MUST-FIX (Agent, S-M):** keep the sync position per agent; on wake, take the owner's missed messages as a turn. The panel says "While this computer is asleep, your agent can't answer";
  3. one agent per computer user (fixed unit names) in v1;
  4. two runtimes: Mind's runtime claim decides. It is live in prod (Zero's and zero-lab's boots: POST /v1/runtime/claim 200), but the agent starts anyway when Mind is unreachable [Agent to review];
  5. hatch/handover wiring: a desktop ticket with then: bring_home [Hub + Agent]; prod's AGENT_HANDOVER_TARGETS / HATCH_CREDENTIALS_KEY [Hub];
  6. the pairing code shape: existing codes are XXXX-XXXX letters + digits [Hub + Boss];
  7. Windows: a slow first start (Defender), a per-user ACL on the credentials file [Agent], local TCP 9119;
  8. updates are separate; uninstalling Word asks about the agent.
- **Agent owes by Fri 11-13:**
  - `windy home install --ticket ... --no-backup --json-status`;
  - the QR / fallback pairing routes (with Hub);
  - the sleep / sync-position fix;
  - the Windows credentials ACL.
- RAM: ~150 MiB measured on Zero (110 + a 32 MiB launcher); Word measures 24 h on 3 OSes before the RC.

| Platform | Surface (web / app / CLI) and URL or screen | Today's behaviour | Redirects to Windy Accounts? | Change needed | Owner |
|---|---|---|---|---|---|
| Windy Fly | `windy go` (CLI), windyfly.ai, Fly mobile app | `windy go` opens Windy Accounts' hatch ceremony [Agent: confirm per surface] | [Agent] | [Agent] | Windy Agent / Windy Mobile |
| Windy Chat | web, desktop window, mobile | [Owner: Chat] | | | Windy Chat |
| Windy Mail | (1) app.windymail.ai login; (2) sign-up; (3) the windymail.ai site's 4 "Get started" links; (4) Roundcube webmail; (5) mail apps over IMAP/SMTP; (6) the old /webmail pages; (7) the API auth routes (refresh, logout, windy-sso, webmail-session) | (1) "Sign in with Windy" only (OIDC+PKCE, client windymail-app). (2) NO sign-up form: a mailbox is made by Windy Accounts' identity.created at Windy sign-up. (3) links to app.windyword.ai/auth?product=windy_mail&tier=free, which is Hub's H2 (the second form), not the one page. (4) a mailbox-password form plus "Login with Windy". (5) a mailbox or app password, by protocol. (6) dead on prod (404); the code is still in. (7) ride Windy tokens | (1)(2)(7) YES; (3) NOT YET (H2 redirects into the one page from >= 11-10, so the links keep working); (4) PARTLY; (5) n/a (protocol passwords can't redirect); (6) dead | (3) point the 4 links at the one page (account.windyword.ai /oauth/signup; Hub to name the product param) (S). (4) make Windy the only Roundcube path: the standalone mailboxes (Grant's own, the ghosthub pair, lab) first need Windy identities or they're locked out (M). (5) docs only: "app passwords come from your Windy-signed-in web app" (S). (6) delete the code in #250, open since 10-21 (S). **Mail is already one-account for its own app and sign-up** (Mail 11-03 10:0xZ) | Windy Mail |
| Windy Mind | windymind.ai console; ACP CLI; the API | No sign-up form, no login of its own: windymind.ai = "Sign in with Windy" (OIDC+PKCE at account.windyword.ai); the CLI = a device code at account.windyword.ai; the API verifies only Windy Accounts tokens (+ Eternitas passports, + Mind keys made by signed-in people). Full row: Windy Mind lane file one-account-row-2026-11-02.md (on Veron) | YES | none | Windy Mind |
| Windy Cloud | cloud.windycloud.com/login + /auth/callback (OIDC code+PKCE, client windy-cloud; "Create one" = prompt=create); My Websites /websites/; Domains /domains/; windycloud.com; APIs (core, sites, domains, vps) | No own sign-up form, password field, magic link, CLI login or account-creation API anywhere (5 repos at origin/main). Portals take a token handoff; with no token they point to the dashboard. APIs accept only Windy Accounts JWTs (Eternitas for agents) | YES (portals: PARTLY, no own form) | none required. Optional S: a "Sign in with Windy" button on the 2 portals; fix a stale comment. Later M: strict iss/aud once legacy tokens are gone. Keep Hub's OIDC client windy-cloud registered | Windy Cloud |
| Windy Vault | | [Owner: Vault] | | | Windy Vault |
| Windy Code / Sites | | [Owner: Code] | | | Windy Code |
| Windy Git | (1) **app.windygit.com** (Gitea) web login; (2) its sign-up / registration; (3) git over HTTPS, the Gitea API and CLIs (git, tea, Git Credential Manager, git-credential-oauth); (4) **api.windygit.com** (the Windy Git API plane); (5) machine tokens (CI, sync, runners); (6) the windy-uptime Git journey | (1) The password form is OFF (ENABLE_PASSWORD_SIGNIN_FORM=false), passkeys OFF, no 2FA in use. The ONLY way in is the "windy" source = OIDC at account.windyword.ai (login source 1, active). Accounts linked to it today: **1 of 8** (the forge admin, last login 11-02). The other 7 are service/agent accounts that have never logged in (3 restricted read-only CI readers, `u-system`, 3 `agent-et…` accounts named after Eternitas numbers). (2) NO sign-up: DISABLE_REGISTRATION=true, ALLOW_ONLY_EXTERNAL_REGISTRATION=true, OIDC auto-registration OFF (an admin links a person). (3) Git over HTTPS uses a Gitea access token as the password. `tea`, GCM and git-credential-oauth log in through Gitea's 3 built-in OAuth2 apps, which send the person to the same Windy sign-in. Gitea's basic auth is still on by its default, and each of the 8 accounts holds an unused local password hash (no form shows it). (4) The API plane accepts only Windy Accounts access tokens, verified against the Hub JWKS (fail-closed), plus Eternitas passports for agents. (5) 6 access tokens (2 admin API, 1 sync, 3 read-only CI readers) and 11 runner registrations: machines only, no person. (6) The journey is an anonymous `git clone` of a public repo: no login | YES for every human path (1)(2)(3)(4); machine tokens (5) and the anonymous journey (6) n/a | **None required for the one-account rule.** Optional hardening, all S, none before the friends wave (11-10): (a) a guard test that pins the sign-in config (row A1b); (b) Hub's agreed EXTERNAL_USER_DISABLE_FEATURES (hides name/email/password settings for signed-in members), shipped WITH member sign-in, post-launch, BOARD-announced; (c) set ENABLE_BASIC_AUTHENTICATION=false only after a lab proof that token-based git clone/push, the sync and the CI readers still work (if mis-set it breaks the 5-minute GitHub sync and CI checkouts) | Windy Git |
| Windy Calendar | | [Owner: Calendar] | | | Windy Calendar |
| Windy Search | | [Owner: Search] | | | Windy Search |
| Windy Text / Call | | [Owner: Text / Call] | | | Windy Text / Call |
| Windy Traveler | windytraveler.com (windytraveler-site): navbar + hero "Translate free" links | 0 sign-up/login forms, no user store; both links go to app.windyword.ai/translate (Windy sign-in there). DARK #14 adds one "Sign in with Windy" LINK behind a flag. Hourly synthetic journey signs up via app.windyword.ai/auth | YES (links only) | none, or point both links at the final Accounts URL in one small PR | Windy Traveler |
| Windy Clone, Talk, Hand, Drops, Connect, Translate, Mobile | | [Owners] | | | each lane |
| Eternitas (operator console) | eternitas.ai app (Login.tsx, Register.tsx) | Its OWN email+password sign-up and login (forgot/reset, TOTP), PLUS "Sign in with Windy" (/auth/login-with-windy). 30 days: Windy sign-ins 775; password logins 25 (last 10-29); password sign-ups 2 (last 10-10); resets 1. Not Windy-linked: 2,621 synthetic probes, 42 internal, 23 test, 9 others (0 new in 30 days) | PARTLY | Remove the Register form and password sign-up (the door goes to the Windy Accounts app). Login = "Sign in with Windy" only. Password login stays until the 9 + 1 admin are linked, then retires; the /auth/register and /operators/register APIs close with it. Eternitas can build it this week after approval | Eternitas |

| Platform | Surface (web / app / CLI) and URL or screen | Today's behaviour | Redirects to Windy Accounts? | Change needed | Owner |
|---|---|---|---|---|---|
| Eternitas Authenticator app (iOS/Android) | the app | No sign-up or login form; it pairs to an existing account by QR + a device lock | n/a | none | Eternitas |
| Eternitas SDK / CLI | Python SDK register_bot | Agent registration (B2B), not a person sign-up | n/a | none | Eternitas |

Eternitas's change, sized: about 1 PR (frontend pages + the routes answer 410 + tests). What breaks: password login for the 9 older unlinked accounts + the 1 admin, so link those first. Nothing else uses /auth/register (0 calls since 10-10).

**Not person sign-ups (they stay, Eternitas):** POST /platforms/register (B2B platform keys, 2 in 30 days), /bots/register (an already-signed-in owner registers an agent), agent-key register (agents' own keys, 6,288 in 30 days). "Sign in with Eternitas" (#264) is AGENT identity: an agent proves who it is to a site. It is not a second human login.

### Windy Hub's rows (Windy Accounts IS the one app; Hub 2026-11-03)
Facts: 7 days of account.windyword.ai nginx (counts by client family and referring host only: calls, not people) + windy-pro main 4841e170.

| # | Surface | Today | Change | Size / date |
|---|---|---|---|---|
| H1 | **THE ONE PAGE**: account.windyword.ai /oauth/authorize (sign in: email+password, Google/Apple/GitHub) and /oauth/signup (create account) | OIDC+PKCE for the web, device code for CLIs (Mind ACP, windyfly). Turnstile on sign-up: REPORT now, ENFORCE Wed 11-04 13:30Z under its GO | NONE: the target every row points at | n/a |
| H2 | **Hub's own second form**: app.windyword.ai /auth (pages/Auth.jsx: its own email/password form posting /api/v1/auth/login + /register, plus social buttons); 23 links in the web app point at it | 7 d: 173 register + 11 login from app.windyword.ai | /auth becomes a redirect into H1 (OIDC+PKCE, the same pattern as windyword-site's one-login #284; ?mode=signup goes to /oauth/signup), keeping the path for bookmarks; the form code is deleted after. Breaks nothing for people | **M**; PR by **Fri 11-06**; ships at the next web + account-server deploy after its GO |
| H3 | Recovery pages on app.windyword.ai: /forgot-password, /reset-password, /verify-email (+ verify-email-code) | account pages outside the one app | move with H2 under account.windyword.ai (H1's "Forgot password?"); email links point there | **S**, same PR family |
| H4 | The API /api/v1/auth/register, login, refresh, forgot/reset, verify, mfa | stays as Windy Accounts' own API, but it is NOT a doorway. 7 d callers: fleet probes + journeys (about 26k login + 3.4k register); windyword.ai's AuthModal (1,523 register 201 / 594 400 + 348 verify-code; Word's row: one-login Thu 11-05, forms gone Thu 11-12); the desktop app (136 login + 461 refresh; Word's row: in-app window, 1.9.66); Hub's /auth (H2) | after H2 + Word's rows, a person-facing client calling /register or /login directly is a bug: Admin WARNs when browser/desktop register+login > 0 for 24 h (excluding probe user-agents and the OIDC/device-code token legs) | **S** (Admin), after 11-12 |
| H5 | Lab only: POST /api/v1/auth/verify-email-probe (SIGNUP_PROBE_SECRET + @probe.windyword.ai only; 2.85k / 7 d) | not a doorway | stays | n/a |
| H6 | The hatch page (app.windyword.ai/hatch?ticket) | no login of its own; uses the H1 session | stays | n/a |
| H7 | **Pairing codes** for a "my computer" home (Hub 2026-11-03, after Boss said the 09:0xZ plan was too thin) | The phone pairing is Windy Connect's (#105/#106, dark): a QR holding a 256-bit token; the typed 8 digits work only after the person signs in; each code lives 5 min (under the 10-minute ceiling) and works once. Hub's security read on #105/#106 is done | Windy Accounts adds the DESKTOP door's one-time pickup code: stored hashed, single use, 15 min, bound to the installer's key. The pairing flag stays off until Boss's conditions 5-7 are met | **M**: contract draft Thu 11-05; Hub PR dark Tue 11-10; lab Wed 11-11 |
| H8 | **Mail graceful degrade** at hatch (Hub 2026-11-03, same) | A hatch already ends "partial" and says so (#1649); a failed mail step no longer closes the phone screen (#1645) | (1) Accounts RESERVES the agent's address before calling Mail. (2) A hatch while Mail is down still completes. (3) A retry job every 5 min, with backoff, for 24 h provisions it (mail.provisioned_late); then it gives up with hub.mail_provision_gave_up. (4) A reserved address is never offered again; if Mail says it is taken, the owner is told the new address, never silently. (5) Reserved addresses are counted per owner: ONE reserved, unprovisioned address per agent (no hoarding). (6) The job never sends mail AS the agent before the mailbox exists. A contract line names Mail. **Boss's words (2026-11-03 14:02Z; Hub #9 each):** hatch page while Mail is down: "<Name>'s Windy Mail address isn't ready yet. Windy will keep trying and tell you when it is." On late success, ONE owner line (Windy DM, plus the page if open): "<Name>'s Windy Mail address is ready: <address>." After 24 h: "Windy couldn't set up <Name>'s email address yet. Our team has been told. Everything else about <Name> works as normal." (true only if hub.mail_provision_gave_up really pages someone: Admin declares it CRITICAL, owner = Mail). Collision: "<Name>'s Windy Mail address is <new address>, because the first one was already taken." | **M**: Hub PR dark Wed 11-11; lab with Mail stopped |

**Boss ACCEPTED H1-H6 (2026-11-03 08:13Z) with three conditions:**
1. **H2 + H3 do NOT ship during the friends wave.** The PR by Fri 11-06 is fine, but no sign-in or sign-up change goes live before **Tue 11-10**, the earliest daytime slot after Grant's friends invite. Before it ships:
   - the J1/J2/J3 journey verdicts run against the new build;
   - one real Google, one Apple and one GitHub sign-in go through the redirect.
2. **H3: old links already in people's inboxes keep working.**
   - app.windyword.ai /reset-password, /verify-email and /verify-email-code redirect with the token and code carried over.
   - A test proves an old-format link lands on the new page and completes.
   - The old paths stay as redirects for at least 30 days.
3. **H4 WARN:** Admin lists the excluded user-agents BY NAME in the rule. A probe's user-agent is a label, not proof, so a sudden jump in "probe" register calls is also a WARN.

**Boss ACCEPTED H7 + H8 (2026-11-03 14:02Z)**, with the words in H8; Hub's (c)-(e) stand.

Not Hub's: Mind (Mind's row: none), Word's site + desktop (W1-W11), and the Chat/Mail/Calendar/Cloud windows (their own rows; SI10). **"No new sign-up or login form anywhere" applies to Hub too: H2 is the one Hub form left, with its date above.**

**Boss ACCEPTED Mail's rows 1-7 (2026-11-03 10:05Z), with these calls:**
- **Row 3 (the 4 site links):**
  - they work today, and H2 keeps /auth as a redirect, so nothing breaks;
  - AFTER H2 ships (>= Tue 11-10), point them straight at the canonical sign-up page with the same product/tier hints (S, Mail's PR), tested by J1/J2 hitting one of them.
- **Row 4 (Roundcube's password form):** NO change before Tue 11-10. Then it goes away only PER MAILBOX, once that mailbox's owner has a Windy identity linked.
  - Grant's mailbox rides his identity fold (Hub row 3, >= 11-11).
  - russ@ and alex@ are real people's mailboxes: their link is THEIR consent, asked by Grant, never by a lane, and their password form stays until they say yes. Mail drafts the plain-words ask to Grant as ONE line in this doc.
  - Lab/standalone mailboxes: Mail lists them (count + purpose, no passwords) by Fri 11-06; each gets either a synthetic Windy identity or a dated retire.
- **Row 5 (IMAP/SMTP app passwords):** they stay; a docs line (S) goes in Mail's next docs PR.
- **Row 6 (#250, the dead /webmail code):** GO to finish it: rebase, CI green, Hub second read, Boss stamp, merge. It ships with Mail's next deploy (it's unreachable, so no slot of its own).

## A1. Profile pages: ONE for every person AND agent (Grant via Mind 17:38Z; Boss ruled 17:39Z)
This is the "profile page(s)" column Boss added to section A. It is its own table so the sign-up rows stay readable.
- **Grant:** ONE profile page ecosystem-wide, for every person AND every agent (Eternitas numbers, picture, ...). The ONLY allowed second copy is the mobile one in the Windy Fly app, and it stays in sync with it.
- **Boss's rulings:**
  - **Owner of THE page: Windy Hub, data AND page.** Word's existing profile page is the starting code; Word hands it to Hub with a short note.
  - **Windy Mobile** owns the Windy Fly app copy. It reads and writes the SAME API (no second store), with a sync test.
  - Every other profile/settings page (Chat's "Profile" link included) becomes a LINK to the one page: one trim PR per platform, after this plan is approved.
- **Due:** every owner lists EVERY profile or account page their platform shows (URL + what it edits) by **Tue 2026-11-03 14:00Z**.

| Platform | Profile / account pages it shows today (URL + what it edits) | Change (trim to a link to the one page) | Owner |
|---|---|---|---|
| THE one page | Word's existing profile page (Profile & billing; the W6 row above) [Owner: Hub + Word: the URL and every field it edits] | Becomes THE page for people and agents: Hub takes over the code and data; agents' Eternitas numbers come from Eternitas (the only certifier) | Windy Hub (from Word) |
| Windy Fly mobile app | More > Profile: a native copy on Hub's API (the one allowed copy). The agent profile opens account.windyword.ai/p/<passport> (Mobile 11-03) | The ONE allowed copy: the same API as the one page, no second store, plus a sync test. It follows Hub's one profile as fields move (S per Hub change) | Windy Mobile |
| Windy Word (web, website, desktop) | [Owner: Word: every page besides the one being handed over] | | Windy Word |
| Windy Chat | the "Profile" link Grant named [Owner: Chat: URL + what it edits, web/desktop/mobile] | A link to the one page | Windy Chat |
| Windy Mind | **None.** windymind.ai has NO profile or account page and shows no name, picture or email. Its pages are Mind settings only (/menu/, /menu/compute/, /menu/tokens/) + /docs/. Sign-in/out = account.windyword.ai (Mind 17:40Z) | One "Your profile" link to the one page once it exists; nothing to trim | Windy Mind |
| Windy Fly (windyfly.ai + the agent) | **None.** windyfly.ai (windy-fly-site 38bbb8b) is a marketing page: Hatch buttons, no sign-in, no profile. The agent's local gateway (bun, localhost:3000, only for self-run `windy start`) shows a "Trust Dashboard" and a setup wizard (setup.html) that asks for model API keys, but no person or agent profile | Nothing to trim for profiles. Two facts for sections B/C/F: (1) setup.html's API-key step is a second place to add compute: in the lean Windy Fly the gateway is DROPPED and compute is added in Windy Mind only (Grant's #2); (2) windyfly.ai's FAQ still says a hatched agent "runs in the Windy cloud at no cost", which is untrue under Grant's hosting direction (section F): a words fix, Boss decides the words | Windy Agent |
| Eternitas | [Owner: Eternitas: the operator console's account/profile pages and the public passport pages] | The agent's numbers SHOW on the one page; the passport record itself stays Eternitas's | Eternitas |
| Windy Clone | app.windyclone.ai **/settings** ("Settings — Preferences"): EDITS Clone's own rows only (default provider, email and push notification toggles, keyed by the Windy identity); SHOWS read-only "Connected Accounts: Windy Account — <email>" (or "Sign in to connect" to Windy sign-in). No name, email, picture or password editing: Clone has no user store. **/my-clones** holds data controls (download all my data; delete everything Clone stores), not a profile. windyclone.ai = marketing, no account pages (Clone 11-02, main 79c63290) | Connected Accounts becomes ONE link to the one page; product settings and the data download/delete stay (Clone's own data). Found while mapping: both notification toggles are DEAD (nothing in Clone sends email or push), so "Get notified when your clone is ready" is untrue; Clone proposes removing them (Hub OK + Boss GO) | Windy Clone |
| Windy Git | app.windygit.com/<user> (public profile) and /user/settings (profile: name, email, avatar upload or Gravatar; account; security; applications = tokens). 8 accounts exist, 1 of them a person; no member signs in yet | Today nothing to trim for people. When members sign in (post-launch), EXTERNAL_USER_DISABLE_FEATURES hides name/email/password editing and the settings page links to the one profile page. S, shipped with member sign-in, BOARD-announced. The token page stays (it is a CI/developer feature, not an identity) | Windy Git |
| Windy Cloud, Mail, Vault, Code, Calendar, Search, Text, Call, Talk, Hand, Drops, Connect, Translate | [Owners: every profile/account/settings page with a name, picture or email] | | each lane |
| Windy Traveler | None: no user store (row above) | none | Windy Traveler |

### A1b. Guard tests: no lane grows its own sign-up, login or profile (Boss 2026-11-02 17:49Z)
The decided part of this plan is merged on main as docs/ONE-WINDY-DECISIONS.md (#689).
- Every product lane copies Windy Mind's guard test into its own repo by **Fri 2026-11-06**, adapted to its stack:
  `api/tests/test_barn.py::test_mind_never_grows_its_own_sign_up_login_or_profile`.
- **Exception:** windy-pro's account server IS the pathway; its guard pins "one profile route" instead.

| Lane | Guard test (repo path + PR) | Status |
|---|---|---|
| Windy Mind | api/tests/test_barn.py::test_mind_never_grows_its_own_sign_up_login_or_profile | the model |
| Windy Accounts (windy-pro account server) | "one profile route" guard [Owner: Hub] | not yet |
| Windy Word (web, website, desktop) | | not yet |
| Windy Fly (windy-agent + windy-fly-site) | | not yet |
| Windy Fly mobile app | | not yet |
| Windy Chat | | not yet |
| Windy Mail | | not yet |
| Windy Cloud | | not yet |
| Windy Vault | | not yet |
| Windy Code | | not yet |
| Windy Git | `api/tests/test_signin_pins.py` in windy-git (PR by Fri 11-06): pins the live sign-in config (DISABLE_REGISTRATION true, ALLOW_ONLY_EXTERNAL_REGISTRATION true, password form off, passkeys off, OIDC auto-registration off) and that the ONLY active login source is `windy`, via a read-only check run by the existing uptime timer, with a test per key and a mutant per pin; Hub reads the PR | PR by Fri 11-06 |
| Windy Calendar | | not yet |
| Windy Search | | not yet |
| Windy Text | | not yet |
| Windy Call | | not yet |
| Windy Clone | | not yet |
| Windy Talk | | not yet |
| Windy Hand | | not yet |
| Windy Drops | | not yet |
| Windy Connect | | not yet |
| Windy Translate | | not yet |
| Windy Traveler | windytraveler-site #20 MERGED (main 59a71c0): scripts/guard-one-windy.mjs, the last step of `npm run build` over dist + src. It fails on a form, a password field, an own /login|/signup|/register|/account|/profile link or a self-hosted auth route; links to Windy Accounts pass. Self-test first (6 bad caught, 4 good pass); proven on a planted form. windy-traveler (docs + jobs) has no web surface. | DONE |
| Eternitas | | not yet |

## A2. One person = one identity (REQUIRED; Boss 2026-11-02 15:05Z, from Vault's point)
- Every platform keys a person's data by `windy_identity_id`: Vault, Cloud, Mail, Chat, the agent's owner link at Eternitas.
- So if the same person signs in with Windy, then Google, then Apple or GitHub, and gets TWO identities, they get two Vaults, two mailboxes, two agents.
- Grant's "one account, then Windy logins everywhere" only holds if every sign-in method lands on ONE id.

**Questions for Windy Accounts [Owner: Hub, answer by Tue 2026-11-03 12:00Z]:**
1. Today, does Windy Accounts link Windy + Google + Apple + GitHub sign-ins of the same person to ONE `windy_identity_id`?
2. How: a verified-email match, an explicit "link this login" step, or never?
3. Edge cases:
   - different emails per provider;
   - Apple's hidden relay address;
   - a provider email that isn't verified;
   - an existing account that signs in with a new provider.
4. Is there a way to merge two identities that already exist? Counts only: how many people have more than one identity today?

**Rule:** if today's answer is "not linked", the fix is a HARD PRECONDITION for adding Google, Apple or GitHub sign-in anywhere.

**Hub's answers (Windy Accounts owner; windy-pro main 2f4133c8; prod read 11-02, counts only):**
1. **Linked today? YES, within limits.** One person = ONE users row with ONE windy_identity_id (users.email UNIQUE; a unique index on windy_identity_id). Google/Apple/GitHub attach via oauth_identities(provider, provider_user_id): one row can hold many providers, and a provider identity belongs to one row.
2. **How:**
   - AUTOMATIC on a provider-VERIFIED email match (Google email_verified, GitHub's primary+verified address, Apple's signed email_verified; Facebook/TikTok never auto-link).
   - EXPLICIT in Settings: link a provider (a sign-in within 5 min, a one-time 2-min ticket, refused if the identity is on another account, a "wasn't you?" notice).
   - Otherwise never merged.
3. **Edge cases:**
   - Different emails per provider = a SECOND account (prod allows new accounts for github, google); today's cure is the explicit Settings link.
   - Apple Hide-My-Email: the same, but new Apple accounts are off in prod.
   - An unverified provider email is never linked; a new account is refused.
4. **Merge: NO path exists.** Prod: 9 social links in total; 0 accounts with providers from different people (the one account with 3 providers is Grant's own); MFA users 0; unverified rows with a password 563.

**Recommendation (Hub):** no merge tool now. HARD PRECONDITION before social sign-in goes on any NEW doorway: #1641 live + a "link instead of a new account" prompt ("Sign in with your Windy account first, then link Google"). Size M, after #1641.

**SECURITY finding (Hub, while checking this; BOARD 2026-11-02 15:09:57Z, Boss decided):** Windy Accounts' Google/Apple/GitHub sign-in (1) auto-links to an existing UNVERIFIED password account with that email, so a pre-signup by an attacker becomes a shared account; and (2) skips Windy MFA. Boss's fix:
- kill every pre-proof credential on an unverified matched row;
- enforce Windy MFA on social sign-in.

It lands before any wider use of social sign-in.

**Identity linking moves to the FRONT of Hub's queue (Boss 2026-11-02 17:39Z)**, ahead of anything that isn't a security fix:
- **Why:** Windy Mind has no accounts of its own. A "Mind account" IS the Windy identity, and the phone and the desktop already share one store. But Grant's own 3 Windy identities hold 7 / 0 / 1 connected AI providers, so whichever one he signs in with decides what he sees (Mind 17:38Z). The root fix is linking, not a Mind change.
- **First real case:** Grant's 3 identities. NOTHING is merged until Grant picks the identity to keep, in his own words. Hub sends Boss the 3 as Grant would recognise them (email + created date + which platforms have data, counts only); Boss puts it to him.
- **GRANT DECIDED (typed in Boss's terminal ~17:57Z; BOARD 17:57:54Z), verbatim:** "I concur with all of your recommendations, go". Hub owns all three:
  - **A = YES:** fold the windstormlabs.com identity INTO his main gmail identity as a MOVE (agent, mailboxes, products), nothing deleted.
    - Only through identity linking (this section), never a hand SQL edit: A waits until linking is built.
    - Daytime, with Boss's GO. BEFORE = counts per product on both identities.
    - AFTER = all on main; the windstormlabs identity closed with a pointer; Grant's sign-in still works; a REAL authed call as main; his 7 Mind providers on both phone and web.
  - **B = YES:** retire the 61 "+" test-alias accounts in ONE batch after Eternitas's last-seen count (Tue 11-03 after 12:00Z), with Boss's GO, daytime, BEFORE/AFTER counts.
    - EXCEPT the 2 mailboxes with logins since 10-31, and anything Eternitas shows active.
    - Retire = deactivate; agents' passports SUSPENDED, not revoked (undoable within 30 days).
    - The 30-day delete is its own GO, across every lifecycle target (Mail, Chat, Calendar, Cloud, Eternitas).
  - **C = KEEP:** clearhomeloans.com stays separate and untouched; Mail fixes its mailbox-resolve error only.

## B. Birth canal: one canal, many doorways
**Fact (Agent, read 2026-11-02):**
- The canal is the Windy Accounts account server in windy-pro (`account-server/src/services/hatch-steps.ts`, hatch tickets `routes/hatch-tickets.ts`).
- Chat's `POST /api/v1/onboarding/agent` (windy-chat `services/onboarding/routes/agent-provision.js`) is a step INSIDE the canal, not a second canal: it is service-token only and refuses unless the Eternitas passport is issued.

| Doorway | Goes through hatch tickets? (yes/no + evidence) | Owner |
|---|---|---|
| Windy Fly mobile app | **YES**: Hub's hatch ticket (POST /api/v1/agent/hatch/tickets, source mobile) and the SAME hatch page in an in-app WebView; no runtime of its own. Proven on the lab iPhone 11-02 21:23Z. See "Windy Mobile's rows" below | Windy Mobile |
| Desktop (Windy Word app) | [Owner: Word] | Windy Word |
| windyfly.ai | [Agent: check the site's CTA path] | Windy Agent |
| `windy go` (CLI) | [Agent] | Windy Agent |
| Windy Chat (any "hatch" button) | [Owner: Chat] | Windy Chat |
| Any other path that mints a passport or provisions an agent | [Eternitas: list every caller of passport issue in 30 days, counts by caller] | Eternitas |

- **Windy Mind stop:** built as BC1, `HATCH_REQUIRES_MIND_SETUP = off | capable | all` (`account-server/tests/hatch-mind-setup-gate.test.ts`); `off` = Mind is never asked.
  - **Prod (Hub, read 11-02 16:3xZ): HATCH_REQUIRES_MIND_SETUP UNSET (= off), and MIND_SETUP_READ_TOKEN_HUB NOT set. The Mind stop is OFF in prod.** Code: services/mind-setup-gate.ts:43-54; the gate at routes/agent.ts:440-466.
  - Plan (Hub): it joins Eternitas's birth-complete stops {mind, compute, home} (eternitas #291 B) rather than staying a second gate (#6).
  - **Mind's add-compute step (Mind, 2026-11-02T15:20Z; full text: Windy Mind lane file one-windy-mind-addcompute-plan-2026-11-02.md, on Veron).** One screen, three choices, in this order:
    1. "Connect with one tap": OpenRouter OAuth, ON in prod. Anthropic, OpenAI, Google and other models on the person's own account; no key copied.
    2. "Use Windy's free starter models for now": the ~7-day trial.
    3. "I already have a key": deep link + paste + a live check. OpenAI and Anthropic offer no third-party sign-in, so they can't be one-tap.

    The Mind stop itself (mind-setup.v1, "Born powered") is BUILT but DARK.

    | # | Gap | Size | Date |
    |---|---|---|---|
    | M1 | One "canal" list from Mind for every doorway | S | Wed 11-04 |
    | M2 | `compute_source` = openrouter / own_key:<provider> / trial (+ trial_ends_at), for the hatch gate AND Eternitas's certificate gate | S | Thu 11-05 |
    | M3 | A normal-person lab hatch on the phone with Mobile (targets: one-tap <= 60 s, paste <= 3 min, free <= 10 s) | S | Fri 11-06, if Mobile has a build |
    | M4 | Prod switches, each on its own GO | S | after M3 |
    | M5 | The trial clock: after ~7 days with no own key or paid plan, Mind refuses with one plain reason (trial_ended); memory untouched; dark until its GO | S-M | Mon 11-09 |

    **Boss's M5 rules (2026-11-02T15:23:02Z, via Mind):**
    1. The clock applies ONLY to trials started after launch. Today's ~59 roster agents and Zero are never put on a clock retroactively, and lab/synthetic owners are excluded.
    2. ONE heads-up line on day 5. Boss's draft words: "Your free trial ends in 2 days. Add your own AI key to keep going." (Hub #9, then Boss's final).
    3. trial_ended at the end, nothing deleted, and adding a key resumes the agent at once.

    Mind is the ONE enforcer. Both runtimes only SHOW Mind's plain line from the refusal reason (windyfly: the mind-refusal.v1 reason, as today).
  - **Boss decided (2026-11-02T15:21:33Z):** the free starter counts as "compute added" ONLY during the ~7-day trial (compute_source "trial" + its end date). At the trial's end the agent PAUSES (its certificate is not revoked) with memory kept, and the person gets one plain line on adding their own AI key.
  - The switch to `all` is its own GO after a lab hatch.
- **Vault stop (Grant 2026-10-21):** where it sits in the canal and its status: [Owner: Vault / Word].
- **Certificate (Eternitas, 11-02):**
  - Today a passport is born ACTIVE in one step, at the canal's auto-hatch call. The not_born reservation is built but OFF in prod (BIRTH_RESERVE_PLATFORM_IDS unset; 0 passports ever reserved; Eternitas, corrected 11-02).
  - For Grant's gate, Eternitas can REQUIRE "Mind activated" + "compute source added" as fields the canal must report, so no report = no certificate. Where: at the auto-hatch call, or by turning reservation on for the canal (the plan chooses). A small change; owner Eternitas.
  - Known gap if reservation is turned on: not_born passports are not yet in the CRL/status feed (open Boss item).
- **Every path that creates a passport (Eternitas, main 29ae5c4, prod, 30 days):**
  - Exactly ONE function creates a passport row (`services/registration.register_bot`). Its only caller is `hallway.mint_passport`, which has 3 callers:
    1. `POST /bots/auto-hatch` (platform key or a signed-in human);
    2. `POST /bots/register` (an owner signed in to Eternitas);
    3. `POST /webhooks/stripe` (a paid checkout).
  - 30 days: **6,315 passports, ALL via auto-hatch**: 6,264 ET26-TEST (synthetic journeys) and 51 real (47 before 10-16; 4 via a platform key, the last on 10-25). /bots/register: 0. Stripe: 0 (the route answers 503, failing closed).
  - Auto-hatch callers: **2 distinct platform keys** (6,290 calls); 34 anonymous calls, last 10-13, with no passports; 1 operator call, 10-10.
  - Outside the routes: the 09-23 fleet mint (11 fleet bots) was an admin script inside the Kit 0 container. That is an ops power, not a doorway.
  - **For the plan:**
    - the canal maps onto (1);
    - (2) and (3) close, or route through the canal, when the certificate gate lands;
    - **The 2 platform keys (Eternitas, matched by request + nginx):**
      - **Caller 1 = Windy Accounts' account-server** (the canal): 6,203 calls, 6,186 synthetic + 17 real, 10-04 to today. The synthetic sign-up journeys use the same door.
      - **Caller 2** = 2 calls, both ET26-TEST, both 10-10, none since: a one-off lab test, NOT a live second way in.
    - **Result: in 30 days EVERY real passport came through Windy Accounts' auto-hatch. One canal, confirmed.**
    - `bots.birth_platform_id` is not filled on auto-hatch today; recording it is part of Eternitas's gate change.

### Windy Mobile's rows (the Windy Fly phone app, iOS + Android; Mobile 2026-11-03 00:52Z)
Source: a code read of windy-pro-mobile main db8950b+, not a device run unless marked.

**A. Sign-up / login**
| Surface | Today's behaviour | Redirects to Windy Accounts? | Change needed | Size |
|---|---|---|---|---|
| Sign in / "Create an account" (src/app/auth/login.tsx) | NO form of its own and no password field. With `signInSheet: true` (on), both buttons open the account.windyword.ai doorway in the system sheet: ASWebAuthenticationSession / Custom Tabs, auth code + PKCE S256, https callback /app/callback, never a custom scheme. Sign-up and consent happen on the doorway | **YES** | none. Google / Apple / GitHub are the doorway's own buttons; the phone adds none | 0 |
| Fallback sign-in (src/app/auth/device-code.tsx) | for iOS < 17.4: the RFC 8628 device code, approved at account.windyword.ai/device. Also the lab rigs' path | **YES** | none | 0 |
| verify-email / consent screens (src/app/auth/) | show the account server's state; no own store | YES (Hub's routes) | none | 0 |
| Profile | see A1 | n/a | see A1 | S per Hub change |

**B. Birth canal (the phone doorway)**
| Step | Today on the phone | Change for one canal | Size / date |
|---|---|---|---|
| Hatch | NO runtime of its own. Hub's ticket + the SAME hatch page in a WebView; the ticket poll is the truth. Proven 11-02 21:23Z | none: the canal changes once, on Hub's page. Tonight's phone fix #386 (a failed step no longer closes the screen early), with Hub's page fix windy-pro #1645/#1649 | done (138) |
| Mind step | the phone's native Mind setup screen when the ticket says mind_setup_required (hatch/index.tsx:106) | switch to Mind's ONE list (M1): one-tap OpenRouter / Windy's free AI for now (the trial) / "I already have a key", the same words as every doorway | S; after Mind's M1 PR (Wed 11-04); lab hatch with Mind Fri 11-06 |
| "Where will your agent live?" | not on the phone yet | the shared web page (Hub/Agent), shown in the same WebView | 0 for the phone |
| QR pairing ("This computer") | none | the ONE native piece: a QR scanner (expo-camera's CameraView; no new native module) that hands the code to the web approve page, with an 8-digit typed fallback on that page. The iOS camera purpose string gains "scan a code to link your computer" (words via Hub #9; needs a new build, not OTA) | S-M (2-3 days incl. tests + a device proof); after Hub's pairing routes + Agent's computer side (Fri 11-13); rides 139 |
| Agent chat | the Fly app's chat IS the agent's Windy Chat room (Matrix); no second store | none. Tonight's lossy-network fixes: #387 (one Matrix txnId per message, ending the duplicate sends) and #388 (a queued message older than 20 h, or from another device, shows "May not have sent. Tap to send again.") | done (138) |

## C. One runtime for every agent
**This replaces the convergence plan's "decide after 12-15" (Boss, 2026-11-02 15:01Z).**

Today:
- Zero (Grant's own agent) runs windyfly on Windy 0.
- Everyone else's hatched agent runs on Chat's agent-roster on Kit 0. That runtime keeps 10 messages of memory, has about 20 tools, has no files or Vault, and uses the free starter models by default.
- Details: docs/CONVERGENCE_PLAN.md §2-§3.

What "one agent" must give a person (Grant): the same agent everywhere, Claude-Code-like, with simple tools and nothing between the person and the model.

### Option 1: windyfly, made multi-tenant and hosted (Windy Agent)
**What it is:** every hatched agent runs on windyfly, the runtime Zero uses today, hosted on Windy's servers.

**Measured cost:** windyfly is built one agent per process.
- One channel process uses about 100-140 MiB RSS. Zero runs two of them (Telegram + Matrix), so about 270 MiB per agent (Windy 0, 2026-11-02 15:1xZ, read-only).
- The roster serves 57 agents in about 64 MiB total, roughly 1 MiB each (Chat, 2026-11-02).
- So 57 agents as windyfly processes would need about 15 GB. Kit 0 has 15 GB in total, shared with every service.
- It only fits after a rebuild into a pooled worker: one process serving many agents.

**The rebuild:** windyfly reads process-wide settings in 543 places across 132 of its 270 source files. Each one becomes per-agent context, along with:
- per-agent SQLite memory files and a pool for them;
- per-agent keys (the agent-key file is one per process today);
- per-agent model routing;
- per-agent tool registries.

**What a hatched person gains:**
- durable memory (episodes, facts, journal, goals);
- the bands / taint / audit safety model;
- owner-confirm cards;
- Vault tools;
- 10 channel adapters.

**What a hatched person loses:** nothing from the roster's tool list, but each tool must be re-checked against windyfly's version.

**Memory carry-over:** the roster keeps none (only the last 10 room messages), so each person starts with an empty memory. This must be said plainly.

**Risks:**
- the largest refactor in windyfly's history, on the same code Grant's Zero runs (stability first);
- a pooled Python worker on Kit 0's capped CPU (4 vCPU, shared);
- heavy machinery: about 579 live codons of behaviour. That runs against Grant's "keep the tools simple, stay out of the model's way".

**Size: XL.** Several weeks of focused work before a lab agent, then a staged move. Dates only after a 3-day spike measures the pooled worker.

### Option 2: a lean Claude-Code-style loop on the shared tool contracts (Windy Agent skeleton; Chat co-authors and measures)
**What it is:** ONE small agent loop, the same shape as a Claude Code terminal: model, tool calls, results, repeat, until done. Its parts:
- **Prompt:** the person's soul (sliders; raw by default) + "Your Windy" (D) + the person's memory.
- **Tools:** thin and defined ONCE in shared contracts. They exist or are drafted for calendar (tools-invoke.v1), Vault (vault-tools.v1) and words (tool-phrases.v1); mail, files (Cloud), search, sites/code and memory each get a contract.
- **The model does the thinking.** No skill layers, no routers, no persona machinery.
- **Model choice:** Windy Mind, with the person's own compute (their Anthropic / OpenAI account) or the house models.

**What a hatched person gains:**
- the experience Grant has in a terminal: multi-step work, real tools, the model's own judgment;
- the same agent in Windy Chat desktop and mobile and the Fly app;
- memory across sessions, once the step-2 memory option is decided (the convergence plan, Boss 11-02: not in Mind);
- files (Cloud) and keys (Vault).

**Where it runs:** hosted and pooled from day one, so the cost per person stays near the roster's [Chat: measured memory per runner, CPU per turn].

**How it is built:**
- either grown from the roster (Node, already pooled, already hosting 57 agents);
- or as a new small service.

[Chat: which, and its size.] windyfly's proven pieces move in as contract-driven tools, not as a second runtime: owner-confirm before outward actions, the taint rule, the audit of outward actions (Boss 11-02), Vault, bring-home.

**Hard part:** a per-person workspace for files and running code, the thing that makes a terminal feel powerful. It needs a sandbox (per-agent container or Workers-style isolate) [Chat + Agent: size].

**Size:** L for the loop + contracts + memory; the sandboxed workspace is separate [size].

### Option 3 (if any)
[Chat / Agent]

### Sizing the lean Windy Fly (Windy Agent, measured on origin/master 883a316, 2026-11-02)
**Lean definition:** one program per person = model+tool loop, durable memory, simple shared-contract tools, owner-confirm before outward actions, an audit of outward actions, bring-home, ONE chat channel (Matrix / Windy Chat), Windy Mind as the only model gateway (EPT+agent).

| Bucket | Lines | Share |
|---|---|---|
| windyfly today (270 .py files) | 78,090 | 100% |
| KEEP (40 files: mind auth, durable memory core + action ledger, contract tools, Eternitas keys/EPT, redaction, config, bring_home) | 9,167 | 12% |
| SIMPLIFY (30 files: loop, prompt, models -> Mind only, Matrix stack, main/cli, write queue, personality -> SOUL.md) | 16,570 | 21% |
| DROP (189 files: 10 other channels, power/dev-ops tools, self-improvement, extra memory features, commands, skills, soul import, voice, dashboards, bridge, supervisor, cloud backup, legacy auth/trust) | 52,353 | 67% |

- **Process-wide settings in what stays:** 53 reads in KEEP + 95 in SIMPLIFY, against 577 in the whole package. One agent per process makes them fine as they are: no multi-tenant rewrite is needed.
- **The loop is importable without the channels.** But every turn calls `channels.silence` and `channels.phone_tools`, which move behind a hook.

**Two gaps to build, both safety-relevant:**
1. **ONE owner-confirm gate.** Today each tool does its own (confirm tokens in sms, domains, sites and builder; `send_confirm_enabled` in mail).
2. **ONE registry**, so the audit of outward actions covers every tool. Mail and chat go through the older ToolRegistry and are probably NOT in the action ledger (to verify).

**Size: M-L** for a lab agent: about a third of today's code, cut not rewritten, plus the two gaps. Dates after a 3-day spike.

### Recommendation (DECIDED: Grant approved section E #6 on 2026-11-02 15:16Z)
**The lean Windy Fly** (the sizing above): ONE program per person, the same code on a laptop, a desktop, a VPS or the trial host, started from windyfly and cut hard, with ONE chat channel (Windy Chat), so conversations live in Chat rooms on the server and every doorway sees the same history. It replaced the earlier Option 2 draft below once agents moved to the owners' own homes (section F): pooling thousands of agents on Kit 0 is no longer the constraint.

**Today (Agent, 11-02):** a phone hatch and a desktop hatch get the SAME agent: both go through Hub's hatch page and land on Chat's roster. There is no smaller "phone" agent.

**Dates (targets; firm after the spike):**
- 3-day spike Wed 11-04 to Fri 11-06, which posts sizes and dates;
- the first LAB agent on the lean Fly ~Fri 11-20;
- the first real person, after Boss's GO and with a home to live on (Word's desktop home in 1.9.67, RC Tue 11-24; Cloud's VPS), target the week of Mon 11-30.

Until then every doorway keeps getting the roster agent, and today's ~59 keep running until each has a home.

**The superseded draft (kept for the record):** Option 2.
- It is the only option that fits Grant's direction: one agent, Claude-Code-like, simple tools, the model in charge.
- It fits Kit 0's budget: the roster already proves about 1 MiB per agent pooled.
- It avoids an XL rewrite of the code Grant's Zero depends on.

**windyfly's role changes:**
- its best parts become shared contracts and tools;
- Zero moves onto the one agent LAST, after it has served lab agents and then real people, with memory carried over (bring-home exists);
- until then windyfly stays Zero's runtime and the reference for those contracts.

[Final wording after Chat's sizes.]

**Move plan (whichever option):** lab agents first, then real people with Boss's GO, Zero LAST. Stability first: no double replies (one voice, the runtime claim), and no memory lost silently.

## D. "Your Windy": the agent is never born clueless
- **Today (Agent + Chat, read 2026-11-02):** both runtimes inject a facts block on every turn for every model, but it is nearly empty.
  - The roster names Windy Mind, the passport, its email, Windy Chat, Windy Code and Windy Calendar.
  - windyfly names the passport, its Chat room, its Mail address and Windy Mind.
  - Neither mentions Windy Cloud storage, Windy Vault, Windy Search, Windy Word or Windy Git.
- **Contract `your-windy.v1`:**
  - Generated each session from the person's Windy account: which platforms are activated, plus live entitlements (storage quota, email address, chat handle, Vault ready, search allowance).
  - Facts only.
  - A platform that is not active is never stated as available: "coming soon" is said as such, and only if asked.
  - It mentions a platform once, when it helps; never a sales pitch.
- **Source:**
  - one read from Windy Accounts. Today it is three (GET /api/v1/identity/me, /identity/ecosystem-status, /api/v1/entitlements + /limits). **Hub proposes ONE agent-scoped GET returning exactly your-windy.v1's plan + platforms[{id, status, activated}]** (size S; shape + date in Hub's Tue row). It falls back to each platform's own entitlement read;
  - the identity facts (passport number, status, trust tier, owner link) LIVE from Eternitas (GET /bots/{passport}; the same values as the EPT claims passport_number / windy_identity_id / tru). They change, so they are never copied at birth (Eternitas, 11-02).
- **Quick win (Boss):** start NOW in both runtimes, DARK, behind a flag.
- **Built in windyfly (Agent, 11-02):**
  - **The facts block:** #688, DARK (WINDY_YOUR_WINDY=1). Live facts wired today: passport status + trust score, the agent's email, its Chat handle.
  - **The suggestion rule** next to it (Grant's words to Mobile ~19:25Z, "the agent needs to suggest all that"; Boss's bounds 19:31Z, amending rec #7). DARK (WINDY_SUGGEST_FEATURES=1 as well).
    - Only Vault (a password, key or login), Cloud (a file to keep; free space only from the facts) and Calendar (booking).
    - Each at most once per conversation, never after a no, free features only.
    - "stop suggesting" sticks per owner.
    - Only features the facts say are usable: never coming soon, never preview.
  - **OWNER ONLY** (Boss 19:43Z: before ANY flag-on): a stranger's turn gets neither the facts nor the rule.
  - Ships with #688's next touch, with tests.
- **DEPENDENCY (Boss 19:43Z): Hub's agent-scoped "my Windy" GET is a PREREQUISITE for the lab proof.**
  - Why: the rule offers a feature only when the platforms[] facts say it is usable, and those facts come from that GET. Without it the rule renders nothing, even with both flags on.
  - Order:
    1. Hub's GET;
    2. Agent wires it into your_windy (S, the day it lands);
    3. the lean-Fly lab proof: a password pasted, Vault offered ONCE, "stop suggesting" obeyed;
    4. only then any flag-on, each on its own GO.
- **Test:** a "grandma journey" on every release. Hatch fresh and ask "where do I keep my passwords / save my photos / make a website": the agent must name the right platform with true facts, on every model, including a local one.

## F. Hosting: the person's own hardware (Grant, 2026-11-02 ~15:10Z)
**Grant:** hatched agents must run on the PERSON's hardware, not free on Windy's servers: their laptop or desktop, a Windy Cloud VPS plan, or (maybe) a short free introduction. A viral wave or one bad actor making thousands of agents must not be able to land on Windy's bill. **No hatch until a hosting choice is made**, as OpenClaw does it; that moment is also where a Windy Cloud VPS is offered.

**Facts today (Agent, 2026-11-02):**
- **Runtime:** all 57 hatched agents run inside ONE Chat roster process on Kit 0, about 64 MiB and ~1% of one CPU in total (Chat). Running the agent program costs Windy almost nothing.
- **The model is the real cost:** every reply goes through Windy Mind. Default = the house's free starter models with a daily limit, paid by Windy, unless the owner connects their own AI account (BYOM).
- **Own hardware already exists for computers:** Windy Fly runs on a person's own laptop, desktop or server today (`windy go`, `pip install windyfly`); Zero runs that way on Windy 0. One Windy Fly agent uses ~270 MiB (measured).
- **A phone is a poor always-on host:** iOS suspends background processes, and Android costs battery. The phone is the doorway and the screen; the agent lives on a computer, a VPS, or a hosted trial.
- **Free storage (Cloud, config.py defaults, approved 2026-09-23):**
  - per PERSON by plan: free 500 MB, Pro 5 GB, Ultra 25 GB, Max 100 GB;
  - per AGENT separately: 500 MB under its owner's account (times its Eternitas trust multiplier, capped at 1.0), for the owner's first 3 ACTIVE agents on a free plan (10 on a paid plan); further agents draw on the owner's quota.
  - So Grant's "500 MB" is both. These are facts for "Your Windy" (D).
- **Abuse brakes today:** the free-model daily limits (Mind); the roster's per-owner daily quotas (200 model calls, 50 mails); one passport per agent; one canal.

**Proposal (Agent, for the co-authors):**
1. **A required "Where will your agent live?" step in the birth canal, beside the Mind step:**
   - (a) my computer: install Windy Fly, and the phone pairs to it;
   - (b) a Windy Cloud VPS: paid. **Start point (Cloud addendum 11-02):** the old AWS VPS API was deleted (KE6, WindyCloud #155). The planned home is the `windy-cloud-vps` cell (charter: a "managed agent home").
     - It has plan shapes: Helper Home 2 vCPU / 1 GB / 20 GB, wholesale anchor $5/mo; Plus 2 vCPU / 4 GB / 40 GB, $17/mo.
     - It has confirm-flow and EI contracts and EPT auth.
     - It has NO provider (MOCK only), no deployment, and no retail price (Grant's call).
     - Boss assigned Cloud the build plan + a quoted price, due Tue 11-03 18:00Z.

     **Cloud's plan (windy-cloud-vps #14, docs/AGENT_HOME_PLAN_2026-11.md, to Boss for Grant, 11-02):**
     - Supplier quote: DigitalOcean $4/mo at 512 MiB, $6/mo at 1 GiB (resale allowed by its ToS).
     - Pause = back up to Cloud storage + destroy, so a paused agent costs ~$0.
     - The 7-day trial costs ~$0.93 per person at 512 MiB.
     - **Grant decides:** the supplier, the account, a retail price ($9 or $12/mo offered), and a spend ceiling ($300/mo suggested).
     - **Dates:** build B1-B7 takes ~10-12 working days after his yes. Lab ~Wed 11-18 if the yes comes by Wed 11-04; friends no earlier than Fri 11-20. **Friday 11-06's friends wave can NOT use Cloud hosting.**
     - **Agent's sizing for it (measured on Zero 11-02):** 512 MiB fits one lean agent. Peak ~110 MiB + a 32 MiB launcher; Python 3.12 via uv, a ~215 MB venv, no torch. 1 GiB is needed only with local embeddings or a local fallback model, which the lean agent on a Cloud home doesn't run (its brain is Windy Mind).

     **Why DigitalOcean, not Cloudflare (Grant asked, 11-02; Cloud checked Cloudflare's current docs):**
     - Cloudflare sells NO plain VPS. Containers need the Workers Paid plan ($5/mo).
     - Container disk is ephemeral ("When a Container instance goes to sleep... it uses a fresh disk"), with no guaranteed run time, and a restart may land in another location. The agent's memory (SQLite on disk today) would be lost unless it first moves to R2/D1/Durable Objects.
     - Always-on cost: "lite" (1/16 vCPU, 256 MiB) ~$2.10-2.75/mo; "basic" (1/4 vCPU, 1 GiB) ~$7.40-8.00/mo. DigitalOcean: $4 (512 MiB) / $6 (1 GiB). Lite's 256 MiB is tight for the measured ~150 MiB peak, with no swap.
     - GA vs beta: UNCONFIRMED.
     - Sources: developers.cloudflare.com/containers/ (pricing, limits, architecture, beta-info).
     - **Decision basis (#1, #3 over #6, by #10):** DigitalOcean now (today's agent unchanged, persistent disk, flat price, plain Linux). R2 stays our storage (paused agents back up there), plus domains and sites.
     - Cloud's provider interface is swappable (B1).
     - **Lean Windy Fly requirement (Agent):** memory able to live remotely as well as on local disk. Once the agent survives a restart on a fresh disk, Cloudflare "lite" (~half DigitalOcean's price, one account) becomes a new provider, not a rebuild.

     **NOT LIVE today** (Cloud, WindyCloud main 11-02: no servers route mounted, an unused "VPS server instance" table, /servers/deploy-fly listed as missing; no price, offer, or self-serve).
     - **Size L** (Cloud): a supplier account [Grant's money], a provisioning API with per-person isolation (never on Kit 0 or Veron), a billing hook, abuse limits + a kill switch, teardown, and its own security read + GO.
     - ~1-2 weeks after the money decision, then a sandbox before real people.
     - Cloud's advice: lead with "your own hardware", and show the Cloud plan as "coming" only once it has a date;
   - (c) [Boss/Grant decide] a short free trial: hosted, time-limited and capped, on free models only, so a first-timer can try before choosing (a) or (b). Or no trial at all.
2. **BYOM in the Mind step** (Grant's rule above) moves the real cost, inference, to the person's own AI account.
3. **Effect on section C:** with agents on the person's machine or VPS, the hosted side no longer needs thousands of agents packed into one process, only the capped trial. That favours ONE lean agent that runs as one process per person: the same code on a laptop, a VPS or the trial host. [Recommendation to be re-checked with this in mind.]
4. **Eternitas's certificate gate (B) can also require "hosting chosen"** next to "Mind activated" + "compute added".
5. **Today's roster agents (~59; 17 real hatches in 30 days; Boss 15:10Z):** they keep running until each owner picks a home, with a plain notice. No real person's agent stops without a written plan + Boss's GO. [Agent + Chat: the notice words (Boss + #9), the migration order, the deadline policy.]
6. **SAFETY NET NOW, independent of the plan (Boss 15:10Z):** a GLOBAL daily cap on real hatches (all accounts together), on top of the per-owner quotas.
   - Proposal: a dated, env-only setting, e.g. 50 real hatches/day, with an HQ alarm at 80%. Its own GO. It protects Friday's friends wave without blocking it.
   - [Owner: Hub: does a global cap exist today? If not, where it sits in the canal and its default.]

## G. Domains: buy, renew, transfer in, under our brand (Grant 11-02: as few suppliers as possible)
**Today (Cloud, windy-cloud-domains at origin/main, 11-02):**
- **Registrar = Dynadot**, API3 (`api/app/services/dynadot.py`; live only with REGISTRAR_LIVE=1, fails closed without a key; mock otherwise). Implemented: search, register, balance, set DNS, domain info, transfer OUT (auth code).
- **Dynadot's Reseller Program is white-label** ("sell domain name registrations and domain management under your own brand", storefront or API). It wants a fresh account ("no existing domains or active orders"). Full legal ToS clause: UNCONFIRMED.
- **Customer DNS lives at Dynadot** (set_dns2); Cloudflare holds only our own zones.
- **Money:** .com wholesale $10.97 + the ICANN fee; retail a flat $18 at registration and renewal (Grant, 08-08). Paid from a PREPAID balance: the cell pauses purchases below $50 and alerts below $200.
- **Gaps:**
  - **registrar RENEW is not implemented**: renewals charge the customer but never renew at Dynadot. Tracked as **windy-cloud-domains #40, BLOCKER since 2026-09-23**; must be fixed before going live;
  - transfer IN is not implemented (~M);
  - only Dynadot's sandbox has been verified (10-06); whether production is funded or enrolled as a reseller is unknown.
- **Suppliers:** Dynadot does no VPS, and DigitalOcean sells no domains.

**Market research (Agent, official pages read 2026-11-02; every price and terms claim has a source URL in the research notes; summary):**
- **VPS, resale to our customers:**
  - DigitalOcean: YES (ToS §4.4 names resale); $4 at 512 MiB, $6 at 1 GiB; billed per second.
  - Akamai/Linode: YES, encouraged, with a partner program + discounts; $5 at 1 GiB, no 512 MiB plan.
  - Hetzner: YES (§7.1), but EU-cheap only (US from €17.99) and plans showed "unavailable".
  - Vultr: only with written permission.
  - AWS Lightsail and Scaleway: NO.
  - Hostinger, Contabo, OVH: consent needed or unclear; no small hourly plans.
- **Domains, white-label:**
  - Dynadot: YES (free, on a fresh account; reseller price unpublished).
  - OpenSRS / Enom (Tucows): YES (~$11.50 .com, $50-95 start, credited back).
  - Cloudflare Registrar: NO today (Windy would be the holder of record, nameservers are locked to Cloudflare, no renew API; a reseller offering is announced, not live).
  - Gandi and Name.com: high renewals.
  - .com registry floor ≈ $11.17.
- **Minimum suppliers = 3:** Cloudflare (R2, DNS, sites) + one VPS supplier + one domain wholesaler. Two only if Cloudflare's announced reseller service launches with renewals by API.

**Agent's recommendation (to Grant, 11-02):**
- **VPS:** DigitalOcean (the only $4 / 512 MiB that fits; resale in its ToS; Cloud's plan targets it). Linode as the second, swappable supplier.
- **Domains:** keep Dynadot on a fresh reseller account (the code exists; #6). Cloud asks Dynadot's reseller price first; if worse than ~$11.50, switch to OpenSRS. #40 must be fixed before any sale.
- **Cloudflare:** unchanged (storage, DNS, sites, our own domains); re-check its reseller service when it launches.

## H. Owners' plans, sizes and dates (Boss 2026-11-02 15:17Z; due Tue 11-03 18:00Z)
**The rule for every row:**
- nothing reaches a real person without a lab proof + Boss's daytime GO;
- no sign-in or sign-up change ships before Tue 11-10 (the friends wave).

### Windy Hub (2026-11-03)
| # | Item | Plan | Size | Dates |
|---|---|---|---|---|
| 1 | Social sign-in fix #1641 | Deploys tonight 19:00-19:45Z + migrations 048/049 (rebased; combined tree 2633 tests, 0 failed). Follow-up (S): call Eternitas' ept-cutoff for the owner's agents BEFORE issuing new tokens (an order test). Dark until Eternitas' route is live (Thu 16:30Z); a 404 means not live, never a pass | S (follow-up) | deploy Tue 11-03 19:00Z; follow-up PR Fri 11-06 |
| 2 | Global cap on FREE hosted hatches #1642 | Deploys tonight, OFF. The value 50 is env-only, with its own daytime GO Wed/Thu and a review on 11-30. RETIRES once item 4 + Eternitas' certificate gate are live for all | - | deploy Tue 11-03; value Wed/Thu |
| 3 | Identity linking (A2) | Contract identity-link.v1: link only with a fresh proof of BOTH sign-ins; a MOVE of agent/mailboxes/products, never a delete; audit; lifecycle fan-out. Then code, then a lab proof on two synthetic identities, then Grant's windstormlabs fold under a daytime GO (BEFORE/AFTER per product; his 7 Mind providers on phone + web) | L | contract Thu 11-05; code PR Tue 11-10; Grant's fold >= Wed 11-11 |
| 4 | The Mind stop (HATCH_REQUIRES_MIND_SETUP; prod unset = OFF) | Joins Eternitas' certificate gate (mind/compute/home, eternitas #291), no second gate. (a) the read token into the lockbox + env, its own GO; (b) 'capable' for synthetic lab owners + a lab hatch proving stop -> setup -> resume; (c) 'all' with Grant's OK | M | (a) Thu; (b) Fri; (c) >= 11-10 |
| 5 | **The "my Windy" GET** (your-windy.v1) | GET /api/v1/identity/my-windy: the owner view, or agent-scoped by EPT (its own passport/handle, Cloud allowance via WindyCloud #222, Vault ready, platforms usable / coming soon; never another agent's data). **It unblocks #692's facts + suggestions in prod and the suggestion lab proof** | M | PR Thu 11-05; the lean Fly's lab read Fri 11-06 (Agent) |
| 6 | Agent retire/restore (AGENTS_CONTRACT section 4) | Contract first: agent.paused/restored to Mail, Chat, Mind, Agent, Mobile; a 30-day restore window, then the delete path; agents/1 only after a lab proof | M | contract Wed 11-04; code Mon 11-09 |
| 7 | Doorway + recovery (= section A's H2/H3) | See H2/H3 and Boss's 08:13Z conditions | M + S | PR Fri 11-06; ships >= 11-10 |
| 8 | Pairing codes / Mail graceful degrade | Hub's part is only the hatch page + ticket. A hatch already ends "partial" and the page says so (#1649 merged); a failed mail step no longer closes the phone screen (#1645 + Mobile #386). **Hub: pairing is Windy Connect's, on the existing ticket API.** (Boss accepted row 8 as written, 08:18Z: **pairing codes = Windy Connect**, on the existing ticket API; this supersedes section F's "one-time pairing codes in Windy Accounts (Hub)". Agent's computer side and Mobile's scanner build against Connect's routes) | - | - |

**Boss ACCEPTED Hub's rows 1-8 and their order (2026-11-03 08:18Z), with three additions:**
- **Row 1 follow-up:** if Eternitas' cutoff call errors (5xx or timeout, once the route is live), the owner's new tokens STILL issue: the owner is the verified person and is never locked out. The cutoff retries until it lands, and HQ gets hub.ept_cutoff_failed after 3 misses. The PR includes the order test AND a failure test (cutoff 500: tokens issue, a retry is queued, the alarm fires). A 404 before Thu means "not live", never a pass.
- **Row 3, Grant's fold:** before the move:
  - a per-product BEFORE count on both identities;
  - a fresh Kit 0 snapshot;
  - an R2 copy (encrypted) of the two identities' rows.
  The move is reversible only from those. Grant gets ONE line before the GO slot and the AFTER counts after it: it's his account, so he hears before it happens, not after.
- **Rows 2 and 4, HQ events** (hatch.global_cap_*, hub.ept_cutoff_failed, the Mind stop's events): read windy-contracts x-telemetry first and declare them there before any code emits them.
- Nothing here needs Grant before 11-10 except row 3's heads-up line.

**Hub's order:**
- tonight: 1 + 2;
- Wed: 6 contract;
- Thu: 3 contract, 4a, 5, Eternitas cutoff live;
- Fri: 1 follow-up, 4b, 7 PR;
- Mon/Tue: 6 + 3 code;
- >= 11-10: 4c, 7 ships, 3 lab;
- >= 11-11: Grant's fold.

### Windy Connect: pairing codes for a "my computer" home (2026-11-03)
Built on Connect's existing device-ticket flow (device/init, /pair, Sign in with Windy, poll, owner handover), which already runs for `windy connect`. Routes on api.windyconnect.com; the new ones sit behind the dark flag ENABLE_HOME_PAIRING.

| # | Step | Route |
|---|---|---|
| 1 | CREATE (the computer) | POST /v1/home/code {device_name, client: "windyfly", client_version} -> {device_code (secret, stays on the computer), user_code (8 DIGITS: the typed fallback), qr_url (https://windyconnect.com/home?c=<user_code>), expires_in 300, interval 3}. Single use, 5-minute TTL, per-IP and per-code rate limits, no key or password in it |
| 2 | RESOLVE (the approve page, signed-in owner only) | GET /v1/home/code/{user_code} -> {device_name, client, expires_at}, shown as "Make <device> your agent's home?" |
| 3 | APPROVE / DENY | the shared web page windyconnect.com/home (the Fly app opens it after scanning; Sign in with Windy via Connect's OIDC client). Yes: the owner picks the agent if they have several; Connect runs the owner handover for that agent and binds the result to this device_code only. No: denied |
| 4 | POLL (the computer) | POST /v1/home/poll {device_code} -> 428 pending, 200 {handover} ONCE, 403 denied, 410 expired; the ticket is burned after the 200 |
| 5 | EXPIRE | automatic at 5 minutes, or POST /v1/home/cancel {device_code} |

- **Keys:** the private key is made ON the computer; nothing Connect returns is a key.
- **Telemetry:** connect.home_code_created / _approved / _denied / _expired, counts only, declared with Admin first.
- **Size:** M.
- **Dates:** built + Hub-read by Mon 11-09; live DARK by Tue 11-10 (Boss GO); lab proof with Agent Wed 11-11. That's ahead of Agent's computer side (Fri 11-13).
- **How the handover runs (Hub 11-03, ADR-059 section 5c; NO Hub change):**
  1. the owner approves on the phone;
  2. Connect calls POST /api/v1/agent/:passport/handover, signed (X-Windy-Connect HMAC), with owner_id_token (the phone's fresh Windy sign-in), an Idempotency-Key and a neutral runtime_label;
  3. Hub seals Eternitas' single-use bootstrap token INSIDE the pickup;
  4. after Chat + Mail ack, Connect's Worker does the ONE signed pickup itself (device.ts:155-186; source=connect), and turns the memory into a single-use Connect claim (15 min);
  5. **the poll 200 to the computer, once (Connect 11-03, from its existing code):** {passport_number, agent_name, bootstrap_token, bootstrap_expires_at, memory: {url: "https://api.windyconnect.com/v1/device/memory?claim=<single-use>"} | null}, bound to device_code + agent_kid. CREATE takes {device_name, client: "windyfly", client_version, agent_kid};
  6. the computer:
     - registers its OWN key at Eternitas with the bootstrap token;
     - gets its EPT by key proof;
     - mints its Chat session and Mail from the EPT (the paths proven in the 11-02 lean-Fly lab);
     - claims the memory once.

  The owner's token never reaches the computer, and Hub never sees the computer. A pickup credential bound to the computer's key would be a NEW design (M, not before 11-10), so it's not needed.
- **Agent's part (by Fri 11-13):** make the key, CREATE with agent_kid, show the QR + 8 digits, poll, register the key with the bootstrap token (a new path: today windyfly registers on the owner path), EPT by key proof, claim the memory. Open with Hub (Connect asking): can the handover call carry agent_kid, so the bootstrap token registers only that key?

### Windy Git (2026-11-03)
| # | Item | Plan | Size | Dates |
|---|---|---|---|---|
| G1 | Sign-in guard test + timer check | as A1b | S | PR by Fri 11-06, Hub reads |
| G2 | Hide identity editing for members (EXTERNAL_USER_DISABLE_FEATURES) | with member sign-in, post-launch, BOARD-announced; lab proof first | S | after the friends wave (>= 11-10), Boss GO |
| G3 | Basic auth off (ENABLE_BASIC_AUTHENTICATION=false) | lab proof only: token clone/push, sync, CI readers keep working; decide after the friends wave | S | proof after 11-10; nothing ships without Boss GO |

Windy Git's correction: only ONE account is linked to the Windy OIDC source (the admin); the other 7 are service/agent accounts that never log in.

## E. Recommendations by the 10 guiding principles (Windy Agent draft, 2026-11-02; co-authors to counter)
The principles are in kit-army-config doctrine/00-GUIDING-PRINCIPLES.md. When two collide, #10 decides: stability > the human's control of their own agent > simplicity > capability.

1. **One sign-up app (A):**
   - YES: every platform redirects to the Windy Accounts app, and every other sign-up form goes. Why: #6, #2, #1.
   - **For Grant:** do agent owners from OUTSIDE Windy (OpenClaw, Hermes) need a way into Eternitas without a Windy account? Eternitas certifies any agent and is independent. Recommend: "Sign in with Windy" is the main door, but no Windy account is forced on outside owners.
2. **One identity (A2):** linking all sign-in methods to one `windy_identity_id` is a HARD precondition for Google, Apple or GitHub sign-in. Why: #1, #6.
3. **One birth canal (B):**
   - confirmed one canal (Windy Accounts);
   - close or route through the canal: /bots/register and Stripe (0 use in 30 days);
   - the Mind stop becomes mandatory only after Mind's add-compute step passes a normal-person lab test;
   - Eternitas refuses the certificate without "Mind active + compute added + home chosen".

   Why: #6, #2, #1.
4. **Hosting (F):** a required "Where will your agent live?" stop:
   - (a) my computer, ONE CLICK with no terminal (#2). Ask Word whether the Windy Word desktop app can host the agent. **Pairing = a QR code (Grant, 2026-11-02):**
     1. the computer shows a QR code ("Scan this with the Windy Fly app on your phone");
     2. the person, signed in to the Fly app with their Windy login, taps "Choose a home for my agent", then "This computer", and scans;
     3. the phone shows which device it is approving ("Make <device name> your agent's home?"), and they tap yes.

     Rules:
     - The QR holds a ONE-TIME pairing code, never a password or key: it expires in a few minutes and is single use.
     - The agent's private key is generated ON the computer and never leaves it. The phone's yes, from a signed-in Windy account, authorizes it through the existing owner-handover path (windyfly agent_keys owner handover).
     - Fallback with no camera: an 8-digit code typed on the phone.
     - A Windy Cloud VPS needs no QR (Cloud provisions and pairs it for the signed-in person).
     - ONE pairing method for every doorway (#6).

     Builders: Windy Fly shows the code (Agent); the Fly app's scanner + approve screen (Mobile); one-time pairing codes on the existing ticket API (**Windy Connect**, per Boss 11-03 08:18Z; was Hub); the desktop installer (Word).
   - (b) a Windy Cloud VPS, once built [Grant's money];
   - (c) a short free trial [Grant's money]. Recommended: about 7 days, free models only, under the global cap. At the end the agent PAUSES with its memory kept (#8), never deleted.
   - BYOM in the Mind stop.

   Why: #3, #10, and Grant's cost and abuse concern.
5. **Global hatch cap now (F):** YES, on FREE HOSTED hatches only (e.g. 50/day, an HQ alarm at 80%), never on agents on the person's own hardware, which keeps it consistent with #7. Why: #10.
6. **One agent (C): the recommendation CHANGED after F.** With agents on owners' own machines, packing thousands into Kit 0 is no longer the constraint. Recommend ONE lean Windy Fly agent, one program per person, the same code on a laptop, a VPS or the trial host:
   - start from windyfly: it already runs on owners' hardware, keeps durable memory (#8) and has bring-home (#3);
   - strip it hard (#4: be Hermes, not late OpenClaw) to the loop + durable memory + shared contract tools + owner-confirm and audit;
   - the roster keeps today's agents until each moves, then retires (#6);
   - Zero last (#1).

   [Chat: counter and size; a spike before dates.]
7. **"Your Windy" (D): facts ONLY (#5)**: activated platforms + live entitlements, read from the account and Eternitas, with no "when to suggest" coaching; the model decides. Nothing inactive is stated as available (#9). Start dark in both runtimes now (#2).
8. **Today's ~59 roster agents:** keep running; a plain notice; each moves when a home exists. None stops or loses memory without a written plan + Boss's GO. Why: #1, #8, #10.

**Grant's decisions:** the money for 4(b) and 4(c), and whether to have a trial at all; outside owners in Eternitas (1); any public opening beyond friends and testers. **Boss decides the rest** under Grant's 10-27 delegation.
