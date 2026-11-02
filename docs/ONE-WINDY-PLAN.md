# One Windy: one sign-up, one birth canal, one agent (plan for Grant)

**Status:** DRAFT. Due Tue 2026-11-03 18:00Z, then Boss decides and takes one plan to Grant.
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
| Windy Word (the one app) | app.windyword.ai sign-up; desktop app sign-up | [Owner: Word] | n/a (it is the app) | [Word] | Windy Word |
| Windy Fly | `windy go` (CLI), windyfly.ai, Fly mobile app | `windy go` opens Windy Accounts' hatch ceremony [Agent: confirm per surface] | [Agent] | [Agent] | Windy Agent / Windy Mobile |
| Windy Chat | web, desktop window, mobile | [Owner: Chat] | | | Windy Chat |
| Windy Mail | webmail, app passwords | [Owner: Mail] | | | Windy Mail |
| Windy Mind | windymind.ai sign-up / console | [Owner: Mind] | | | Windy Mind |
| Windy Cloud | | [Owner: Cloud] | | | Windy Cloud |
| Windy Vault | | [Owner: Vault] | | | Windy Vault |
| Windy Code / Sites | | [Owner: Code] | | | Windy Code |
| Windy Git | | [Owner: Git] | | | Windy Git |
| Windy Calendar | | [Owner: Calendar] | | | Windy Calendar |
| Windy Search | | [Owner: Search] | | | Windy Search |
| Windy Text / Call | | [Owner: Text / Call] | | | Windy Text / Call |
| Windy Traveler | windytraveler.com (windytraveler-site): navbar + hero "Translate free" links | 0 sign-up/login forms, no user store; both links go to app.windyword.ai/translate (Windy sign-in there). DARK #14 adds one "Sign in with Windy" LINK behind a flag. Hourly synthetic journey signs up via app.windyword.ai/auth | YES (links only) | none, or point both links at the final Accounts URL in one small PR | Windy Traveler |
| Windy Clone, Talk, Hand, Drops, Connect, Translate, Mobile | | [Owners] | | | each lane |
| Eternitas (operator console) | eternitas.ai app (Login.tsx, Register.tsx) | Its OWN email+password sign-up and login (forgot/reset, TOTP), PLUS "Sign in with Windy" (/auth/login-with-windy). 30 days: Windy sign-ins 775; password logins 25 (last 10-29); password sign-ups 2 (last 10-10); resets 1. Not Windy-linked: 2,621 synthetic probes, 42 internal, 23 test, 9 others (0 new in 30 days) | PARTLY | Remove the Register form and password sign-up (the door goes to the Windy Accounts app). Login = "Sign in with Windy" only. Password login stays until the 9 + 1 admin are linked, then retires; the /auth/register and /operators/register APIs close with it. Eternitas can build it this week after approval | Eternitas |

**Not person sign-ups (they stay, Eternitas):** POST /platforms/register (B2B platform keys, 2 in 30 days), /bots/register (an already-signed-in owner registers an agent), agent-key register (agents' own keys, 6,288 in 30 days). "Sign in with Eternitas" (#264) is AGENT identity: an agent proves who it is to a site. It is not a second human login.

## B. Birth canal: one canal, many doorways
**Fact (Agent, read 2026-11-02):**
- The canal is the Windy Accounts account server in windy-pro (`account-server/src/services/hatch-steps.ts`, hatch tickets `routes/hatch-tickets.ts`).
- Chat's `POST /api/v1/onboarding/agent` (windy-chat `services/onboarding/routes/agent-provision.js`) is a step INSIDE the canal, not a second canal: it is service-token only and refuses unless the Eternitas passport is issued.

| Doorway | Goes through hatch tickets? (yes/no + evidence) | Owner |
|---|---|---|
| Windy Fly mobile app | [Owner: Mobile] | Windy Mobile |
| Desktop (Windy Word app) | [Owner: Word] | Windy Word |
| windyfly.ai | [Agent: check the site's CTA path] | Windy Agent |
| `windy go` (CLI) | [Agent] | Windy Agent |
| Windy Chat (any "hatch" button) | [Owner: Chat] | Windy Chat |
| Any other path that mints a passport or provisions an agent | [Eternitas: list every caller of passport issue in 30 days, counts by caller] | Eternitas |

- **Windy Mind stop:** built as BC1, `HATCH_REQUIRES_MIND_SETUP = off | capable | all` (`account-server/tests/hatch-mind-setup-gate.test.ts`); `off` = Mind is never asked.
  - Prod's value: [Owner: Word/Hub, read it].
  - What makes Mind's add-compute step thumb-ready (sign in with Windy / Google / Apple / GitHub; connect Anthropic, OpenAI and others), and its date: [Owner: Mind].
  - The switch to `all` is its own GO after a lab hatch.
- **Vault stop (Grant 2026-10-21):** where it sits in the canal and its status: [Owner: Vault / Word].
- **Certificate (Eternitas, 11-02):**
  - Today the canal reserves a passport (status not_born), and Eternitas activates it and issues the certificate + EPT at the end.
  - For Grant's gate, Eternitas can REQUIRE "Mind activated" + "compute source added" as fields the canal must report before the passport goes active: no report = no certificate. A small change; owner Eternitas.
  - Known gap: not_born passports are not yet in the CRL/status feed (open Boss item).
  - [Eternitas: confirm no other issuer path is live; counts by passport-issue caller, 30 days].

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

### Recommendation (Windy Agent's draft; Chat to counter)
**Option 2.**
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
  - one read from Windy Accounts [Owner: Word/Hub: does an endpoint exist, and its shape?], falling back to each platform's own entitlement read;
  - the identity facts (passport number, status, trust tier, owner link) LIVE from Eternitas (GET /bots/{passport}; the same values as the EPT claims passport_number / windy_identity_id / tru). They change, so they are never copied at birth (Eternitas, 11-02).
- **Quick win (Boss):** start NOW in both runtimes, DARK, behind a flag.
- **Test:** a "grandma journey" on every release. Hatch fresh and ask "where do I keep my passwords / save my photos / make a website": the agent must name the right platform with true facts, on every model, including a local one.

## E. Decisions for Boss (then Grant)
[Filled when A-D are in]
