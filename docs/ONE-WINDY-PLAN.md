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
| Eternitas (operator console) | eternitas.ai | [Owner: Eternitas] | (Eternitas is its own company: say whether the rule applies) | | Eternitas |

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
- **Certificate:** Eternitas is the only certifier, at the end of the canal: [Eternitas: confirm, and confirm no other issuer path is live].

## C. One runtime for every agent
**This replaces the convergence plan's "decide after 12-15" (Boss, 2026-11-02 15:01Z).**

Today:
- Zero (Grant's own agent) runs windyfly on Windy 0.
- Everyone else's hatched agent runs on Chat's agent-roster on Kit 0. That runtime keeps 10 messages of memory, has about 20 tools, has no files or Vault, and uses the free starter models by default.
- Details: docs/CONVERGENCE_PLAN.md §2-§3.

What "one agent" must give a person (Grant): the same agent everywhere, Claude-Code-like, with simple tools and nothing between the person and the model.

### Option 1: windyfly, made multi-tenant and hosted
[Agent: gains/losses for a hatched person; memory carry-over; move plan; risks; cost per person on Kit 0's capped CPU; size and dates. Starting fact: windyfly reads process-wide settings in 543 places across 132 of its 270 source files (2026-11-02), because it was built one agent per process.]

### Option 2: a lean Claude-Code-style loop on the shared tool contracts
[Agent + Chat: the same fields. The loop is model, tools, results, repeat, with the person's memory and the "Your Windy" block. The tools come from the shared contracts both runtimes already conform to (vault-tools.v1, tools-invoke.v1 for calendar, the phrase table).]

### Option 3 (if any)
[Chat / Agent]

### Recommendation
[Agent, plainly, after the options are sized]

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
- **Source:** one read from Windy Accounts [Owner: Word/Hub: does an endpoint exist, and its shape?], falling back to each platform's own entitlement read.
- **Quick win (Boss):** start NOW in both runtimes, DARK, behind a flag.
- **Test:** a "grandma journey" on every release. Hatch fresh and ask "where do I keep my passwords / save my photos / make a website": the agent must name the right platform with true facts, on every model, including a local one.

## E. Decisions for Boss (then Grant)
[Filled when A-D are in]
