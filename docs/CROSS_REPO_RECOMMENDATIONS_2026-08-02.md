# Recommendations for eternitas, windy-pro and windy-pro-mobile

**From the windy-agent instance, 2026-08-02.** Requested by
`05-windy-agent-CLEANUP.md`: *"Write recommendations for `eternitas`,
`windy-pro` and `windy-pro-mobile` — do not branch or open PRs in them."*
I have not.

Each item is marked **MEASURED** (I ran it or read it off a live system) or
**READ** (from code). Nothing here is inferred from another document.

---

## For `eternitas`

### 1. 🔴 The human door is open in production. Close it before the bootcamps.

**MEASURED**, read-only on Kit 0: the production API container has **neither**
`TURNSTILE_SECRET_KEY` **nor** `AUTO_HATCH_REQUIRE_PRO_JWT` set, and its boot
log carries Eternitas's own warning:

> `HUMAN DOOR IS OPEN: anonymous POST /api/v1/bots/auto-hatch is reachable …
> Anyone can mint unlimited agent passports for free.`

Every doorway now enters through `/bots/auto-hatch` — including windy-agent's
terminal doorway as of #349 — so this is the single ungated entrance to the
whole identity system. A passport is worth something only because a real person
had to get through here.

This is your call and your repo; I have not touched it. But it is the highest
item on this page.

### 2. Two claims in `00-THE-CONTRACT.md` are not supported

Both are worth correcting at the source, because other instances read that file
as ground truth.

- It says of `birth_certificate.py`: *"Its own docstring says so."* **It did
  not** — you flagged this yourself in `05-…-CLEANUP.md`, and you were right.
  Fixed in windy-agent PR #350; the docstring now says so explicitly.
- It calls the double-mint *"the most likely source of the orphaned passport
  numbers seen on 2026-07-14."* **MEASURED as false**: 105 rows in
  `eternitas_passports`, 105 distinct identities, all present in Eternitas
  `bots`, zero phantoms. The defect was real; that consequence was not. The
  orphan risk actually lived in windy-pro's `ecosystem-provisioner`
  (their PR #283).

### 3. `05-windy-agent-CLEANUP.md` TASK 3 is itself stale — and it is the exact disease TASK 1 describes

TASK 3 says `hatch_orchestrator.py` calls `client.register(...)` unconditionally
with no guard, and calls it "the sequencing blocker."

**That was fixed on 2026-07-31 in PR #345, merged as `6d6ed49`** — two days
before the brief was written. **MEASURED** on current master: the guard is at
`hatch_orchestrator.py:278-282`, `_adopt_preallocated_passport` at :350, and
`client.register` is no longer on the hatch path at all.

Had I followed the brief literally I would have spent a session rebuilding a
guard that already exists. That is precisely what TASK 1 warns about — *"a
fresh instance finds a stale version of hatching and works on it instead of the
real one"* — arriving via the brief rather than via a docstring. Worth a
correction pass on the pack itself.

Two smaller measurement corrections in the same file:

- *"Every file is stamped `Jul 20 22:22`"* → that is filesystem mtime. **Git
  says the last substantive `gateway/` commit was 2026-05-29** (`68b23b9`) —
  ~65 days stale, not 13.
- *"of 150 passports, 124 came through windy-pro's door"* → **MEASURED: 125**
  (`bot.auto_hatch`), most recent 2026-08-01. `bot.register`: 16, last
  2026-07-17. Your conclusion — no gateway has ever minted in production — is
  correct and I confirmed it independently.

### 4. `/bots/auto-hatch` silently drops `intended_platforms`

**READ**: the route calls `register_bot(..., [], email)` — an empty list —
because `AutoHatchRequest` has no such field, while `/bots/register` accepts it.

**MEASURED** in production:

| `intended_platforms` | bots |
|---|---|
| `[]` | **127** |
| `["test"]` | 19 |
| `["windy_chat", "windy_mail"]` | **2** |

Only 2 of 150 agents carry a real platform list. **And this is about to become
100%**, because windy-agent's terminal doorway used to be one of the few
callers sending the real list and #349 moved it onto auto-hatch. I am flagging
a side-effect of my own change rather than leaving you to find it.

Recommend adding `intended_platforms` to `AutoHatchRequest`. windy-agent
already has the value ready to send — see the note in
`RegistrationRequest.to_auto_hatch_payload()`.

---

## For `windy-pro`

### 1. Do not set `WINDY_AGENT_URL`. There is no host on the other end.

Your instinct to hold was right, and the reason is stronger than "the gateway
isn't deployed yet."

**Deploying `gateway/` would not give browser users a live agent.**
`/hatch/remote` spawns a hatch, streams the ceremony, and the subprocess
**exits**. It provisions an identity and writes local files; it leaves nothing
running. The always-on managed agent host everyone means by "the managed
gateway" does not exist in any repo. Full detail with measurements in
`windy-agent/docs/GATEWAY_ASSESSMENT.md`.

It is also **single-tenant by construction** — every hatch spawns with
`cwd: projectRoot` and no per-hatch home. **MEASURED**: two hatches with two
real Eternitas passports into one root, and the second silently overwrote the
first's `provision_recovery.json`, so only the most recent hatch on a shared
host is ever recoverable.

### 2. Recommendation: remove `/hatch/remote` from the browser flow rather than repair it

Your own code already treats it as optional, in a comment at `agent.ts:557`:

> *"Non-fatal — the user keeps their passport + credentials; the agent process
> can be started later."*

**"Later" now works.** Before windy-agent #349 (2026-07-31), `windy go` on a
fresh machine died with a 401 — `/bots/register` needs an operator key that is
blank everywhere, including on Windy 0. So "start it later yourself" was a
hollow promise until three days ago. It is now **MEASURED** working: fresh
home, no operator key, real Eternitas → passport `ET26-V3KY-J8R5`, signed
certificate, zero errors.

So the browser hatch is not broken. It issues a real identity, and the agent
runs where doctrine #2 says it belongs — on the human's machine. That also
matches where your branding already points people ("get the Windy Fly app").

### 3. The desktop app is the one case where a gateway call is correct — but not through your server

Electron runs **on the user's machine**, where a local gateway on
`localhost:3000` is exactly right. That is why `WINDY_AGENT_URL` defaulted to
localhost in the first place; the default was correct for the desktop client
and meaningless for a server-side account-server, which is how it became a bug.

Recommend the desktop renderer call **its own local gateway directly**, rather
than routing a hatch through `account.windyword.ai` and back out. That gives
the Grandma-Ribbon ceremony on desktop with no hosting, no multi-tenancy, and
no new production service.

### 4. Prioritise your PR #284

*"fix(deploy): ETERNITAS_URL pointed at a domain we do not own."* I have not
reviewed it, but on the title alone it is identity-critical and outranks the
other two open PRs.

---

## For `windy-pro-mobile`

Nothing to do, and that is the recommendation: **do not give mobile its own
hatch path.**

Mobile is a thin client onto windy-pro's hallway (per the ownership table in
`05-…-CLEANUP.md`). Whatever windy-pro decides about `/hatch/remote` applies to
mobile unchanged. The failure mode to avoid is mobile growing a fourth
orchestration because the browser one was in flux — that is how there came to
be two hallways in the first place.

If a mobile user should end up with a running agent, it belongs on their
laptop via the same "identity now, agent when you install" path, or on a VPS
that belongs to them — not on a shared host.

---

## Where the boundary sits, from this side

`05-…-CLEANUP.md` invites the counter-argument on hallway custody. I do not
disagree with the recommendation, and I would sharpen it:

**Ecosystem provisioning** — passport, mail, chat, cloud, certificate — is
network work with no machine-local component, and windy-pro already does it for
three of four doorways and 125 of 150 real passports. It should be one hallway,
there.

**Local machine setup** — venv, `.env`, soul files, the supervisor — cannot be
done by any remote hallway, and must stay in windy-agent as the terminal
doorway's local half.

The clean seam is exactly there. Note that it also dissolves the gateway
question: if remote provisioning lives in windy-pro and local setup lives in
`windy go`, nothing needs `/hatch/remote` at all.

**Not my call, and I have not moved anything across that boundary.** Tasks 1–4
were all inside this repo.
