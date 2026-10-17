# N9 Agent's Hands: the agent side of phone tools (planning, 2026-10-16)

Windy Agent's codons for "Your Agent's Hands" v3 (Boss 10-16 ~23:55Z; Grant: the agent can do anything on
his phone that the phone allows, the way a terminal with sudo can on a computer). Windy Mobile owns the phone
side (Strand H), Windy Chat the H1 bridge, and Windy Hub the approvals contract (H0.6). This file is the
**agent's** part: how the agent calls phone tools, what it may run without a confirm, and how each request is
run once and wiped. Each codon also has a `planned` row in `STRAND_V2.md` (gene N9); a row turns `live`
only with its test, as for every other gene.

**Builds on what is live (gene N7):** phone tools are dark unless `WINDY_PHONE_TOOLS=1`, owner-only, in an
unencrypted 1:1 room while the phone says it is online. Requests must start within 120 s
(`REQUEST_START_S`), results are waited for up to 600 s, read once, redacted from the room and turned into
one follow-up turn with no tools, and memory keeps only a stub. Today the agent knows two kinds:
`contacts.search` and `sms.compose` (phone-tools 1.1.0, windy-contracts #142).

**Rules for every codon:**
- Only OS permissions the owner grants. Nothing gets around a phone's security.
- Tests on lab accounts and lab phones only, never Grant's.
- HQ sees counts, never content.
- Anything that leaves the phone shows the confirm card.
- The agent never approves its own request.

| Field | Meaning |
|---|---|
| contract | the windy-contracts file and version the codon depends on |
| flag | the switch that keeps it dark until a GO |
| needs | other lanes' codons that must land first |
| done when | the test that turns the strand row `live` |
| size | S (under a day), M (1-3 days), L (more) |

---

### N9.1 Any phone kind the contract defines, not two hard-coded ones
- **What:** the agent registers each tool kind the owner's phone advertises in its `ai.windy.phone_tools`
  state, from the pinned phone-tools contract. It no longer uses the fixed `TOOL_NAMES` pair. Unknown kinds are
  refused. A kind the phone stops offering disappears from the next turn.
- **Why:** H1 adds many kinds (calendar, photos, files, calls, settings, accessibility). Hand-wiring each
  one in the agent would trail the phone by a release.
- **Owner:** Windy Agent. **Where:** `src/windyfly/channels/phone_tools.py`, vendored contract.
- **Contract:** phone-tools v2 (H1, Chat owns the contract; Mobile defines the kinds and their JSON
  schemas). **Flag:** `WINDY_PHONE_TOOLS=1` (existing).
- **Needs:** Chat/Mobile publish phone-tools v2 with a per-kind schema and risk class (N9.2).
- **Done when:** a contract fixture with a new kind shows that kind, with its schema, to the model only when
  the phone offers it, and a kind missing from the pinned contract is refused.
- **Size:** M.
- **iPhone / Android:** the agent is the same for both. Each phone offers only what its OS allows (iPhone
  per-kind permissions and App Intents; Android power mode). The agent shows the model what the phone offers,
  nothing more.

### N9.2 A risk class for every kind, and a confirm wherever it matters
- **What:** each kind in the contract carries a risk class. The agent maps it to a capability `Tier`:
  - `read`: READ_EXTERNAL, no confirm;
  - `draft`: WRITE_LOCAL_SAFE, no confirm;
  - `change` / `delete`: WRITE_DESTRUCTIVE, confirm card;
  - `send` / `call` / `share` / `pay` / `install`: EXTERNAL_EFFECT, confirm card;
  - `ui_action` (accessibility): confirm card unless the action is read-only.

  A kind with no class is refused, never defaulted to "safe".
- **Why:** "do anything" is only safe if the line between "looks" and "acts" is in the contract, not in a
  prompt the model can talk its way around.
- **Owner:** Windy Agent (agent side); Mobile and Chat put the class in the contract.
- **Where:** `phone_tools.py`, `agent/capabilities/descriptor.py`.
- **Contract:** phone-tools v2 `risk` field. **Flag:** `WINDY_PHONE_TOOLS`.
- **Needs:** N9.1.
- **Done when:** a table test maps every class to its tier and confirm rule, and a kind without a class is
  refused.
- **Size:** S.

### N9.3 The confirm card: the owner's tap is the only approval
- **What:** for a kind that needs a confirm, the agent's request carries `confirm: required` and one plain
  line saying what will happen (who, what, how many; no hidden detail). The phone shows the card. The rules:
  - Only the owner's tap on that card approves it.
  - A "no", or no tap before the request expires, means **not done**. The agent says so plainly and never
    retries on its own.
  - The agent cannot set `confirm: none` on a kind whose class needs one; the phone enforces it too.
- **Why:** this is the one rule that makes "do anything" acceptable: nothing leaves the phone, changes or
  is deleted without the owner seeing it first.
- **Owner:** Windy Agent (request + outcome); Mobile (the card); Hub (approvals contract H0.6).
- **Where:** `phone_tools.py`.
- **Contract:** approvals v1 (H0.6) + phone-tools v2. **Flag:** `WINDY_PHONE_TOOLS`.
- **Needs:** Hub H0.6, Mobile's card.
- **Done when:**
  - a lab test with a simulated phone shows that a declined card and an expired card each give "not done"
    and no second request;
  - a request without `confirm` for a `send` kind is never sent.
- **Size:** M.
- **iPhone / Android:**
  - Android: deletes also go through the system's own delete confirm (MediaStore); our card comes first,
    the system dialog second.
  - iPhone: App Intents that send show Apple's own confirmation as well.

### N9.4 Run once
- **What:** every request has a fresh id, and the phone treats a repeated id as already handled. The agent
  never sends the same request again after a timeout, a restart, a lost answer ("outcome unknown",
  N7.12), or a model retry. Trying again needs a new ask from the owner.
- **Why:** a double-sent text or a double payment is worse than a missing one.
- **Owner:** Windy Agent; Mobile honours the id.
- **Where:** `phone_tools.py` (id per request; restart sweep N7.9 extended to all kinds).
- **Contract:** phone-tools v2 `request_id` (idempotency). **Flag:** `WINDY_PHONE_TOOLS`.
- **Needs:** N9.1.
- **Done when:** over every kind, a timeout, a restart and a model re-call each send exactly one request,
  and the second attempt answers with the earlier outcome or "unknown", never a new send.
- **Size:** S.

### N9.5 Wipe: read once, keep a stub, and no raw files in the model
- **What:** every kind's answer is read once, redacted from the room, and becomes one follow-up turn with no
  tools. Memory keeps a short stub (kind, outcome, counts) and never the content (N7.5/N7.6 for every kind).
  - Photos, videos and files are never put into the model as raw bytes. The model sees what the owner allowed
    for that request: a name, a size, a date, or a thumbnail or text extract the phone made.
  - A file the owner asks to store goes to Windy Cloud by reference (Cloud's H5), not through the chat.
- **Why:** the agent's memory and the model provider must not become a copy of the phone.
- **Owner:** Windy Agent. **Where:** `phone_tools.py`, the result turn in `agent/loop.py`.
- **Contract:** phone-tools v2 result shapes. **Flag:** `WINDY_PHONE_TOOLS`.
- **Needs:** N9.1; Cloud H5 for stored files.
- **Done when:** for each kind's fixture, nothing of the answer's content remains in the room, the stored
  episodes or the memory nodes after the turn (a canary string is searched for everywhere), and a file
  result reaches the model only as the allowed fields.
- **Size:** M.

### N9.6 Limits, and a pause stops everything
- **What:**
  - Caps per kind on each turn and each hour. For example, at most 20 texts an hour and 1 call at a time.
    The owner can lower the caps but not raise them past a hard ceiling.
  - When the owner pauses the agent (Mind grant_off, or "pause all"), no phone request goes out and any
    request not yet started is withdrawn.
  - The agent says when a limit or a pause stopped something.
- **Why:** a looping model or a prompt injection must not be able to text the whole address book.
- **Owner:** Windy Agent. **Where:** `phone_tools.py`, the turn budget (silence/turn-budget spec).
- **Contract:** none new. **Flag:** `WINDY_PHONE_TOOLS`.
- **Needs:** N9.2.
- **Done when:** a loop of 25 send requests in an hour sends 20, the rest are refused with the plain line,
  and a pause mid-turn sends nothing further.
- **Size:** S.

### N9.7 What a "do anything" agent may run without a confirm (the policy table)
- **What:** one table in code, tested, mirrored in the v3 page:

| Without a confirm | With the confirm card | Never |
|---|---|---|
| Read the owner's contacts, calendar, notes, reminders | Send a text, email, message or post | Anything that gets around the OS (no work-around for a permission the owner did not grant) |
| See photo and file names, dates, sizes; thumbnails the owner allowed | Place or join a call | Read password fields or type a password into another app |
| Open an app or a screen; set a timer, an alarm or a reminder | Share a photo, file or location outside the phone | Forward one-time codes or passwords off the phone |
| Draft a text or email (not sent) | Delete, move or change anything (files, contacts, events, settings) | Turn off the phone's own security (lock screen, Play Protect, Find My) |
| Search the phone; read the current screen in accessibility mode, with no password fields | Buy, pay, subscribe; install or remove an app | Act on another person's phone or account |
| | Tap a "send / pay / delete / confirm" button in another app (accessibility) | |

- **Why:** Grant's "sudo" model with a seatbelt. The agent acts freely on what only looks, and asks before
  anything that acts on the world.
- **Owner:** Windy Agent (policy in code + tests); Mobile shows it in Settings.
- **Where:** `src/windyfly/channels/phone_policy.py` (new).
- **Contract:** phone-tools v2 risk classes (N9.2). **Flag:** `WINDY_PHONE_TOOLS`.
- **Needs:** N9.2.
- **Done when:** every row is a test case, and the "Never" column is refused even when the owner asks in chat
  (the owner can change OS permissions, not this column).
- **Size:** S.
- **iPhone / Android:** the "Never" column is the same on both. What each phone can do at all is Mobile's
  track (iPhone hard walls: no reading other apps' messages, no background UI control).

### N9.8 Android power mode: accessibility actions through the confirm card
- **What:** when the owner has switched on Windy Mobile's accessibility service (Android only), the agent may
  ask for UI actions as a `ui_action` kind:
  - reading the screen, opening and scrolling run without a confirm;
  - a tap that sends, pays, deletes or confirms in another app goes through the confirm card first, showing
    the app and the button;
  - password fields are never read (Android flags them) and never typed into.
- **Why:** this is the closest thing to "sudo on the phone", so it gets the strictest rule.
- **Owner:** Windy Agent (requests + policy); Mobile (the service, Play risk and direct-download track).
- **Where:** `phone_tools.py`, `phone_policy.py`.
- **Contract:** phone-tools v2 `ui_action`. **Flag:** `WINDY_PHONE_TOOLS` plus `WINDY_PHONE_UI_ACTIONS=1`.
- **Needs:** N9.2, N9.3, N9.7; Mobile's power-mode build.
- **Done when:** lab tests with a simulated phone show a read-only action runs, a "Send" tap waits for the
  card, and a password field is refused.
- **Size:** M.
- **iPhone:** not possible (no system-wide accessibility control for apps). The iPhone path is App Intents
  and the Windy Shortcuts pack (Mobile).

### N9.9 Machines on Windy Mesh: the agent treats a shell as FULL_MACHINE
- **What:** if Windy Code exposes "run on my machine" (a session on an owner node over Windy Mesh) as an agent
  tool, the agent treats it as `FULL_MACHINE`:
  - owner band only;
  - every session start goes through the confirm card on the phone (which machine, which session);
  - no unattended starts;
  - the HQ ledger records start and stop counts only, never keystrokes or output.
- **Why:** a terminal with sudo is the most powerful hand. Its seatbelt is the same as a payment's.
- **Owner:** Windy Agent (tool tier + policy); Windy Code (session broker); Windy Mind (S20 mesh, the new
  "attach to a running session" codon).
- **Where:** `agent/capabilities` (a descriptor for the Code tool).
- **Contract:** Windy Code's session-broker contract (to be named by Windy Code). **Flag:**
  `WINDY_MESH_SESSIONS=1`.
- **Needs:** Windy Code's broker; Mind S20.5/S20.7; Hub's device approval.
- **Done when:** a descriptor test shows FULL_MACHINE, owner-only and confirm-required, and a lab run against
  a lab node starts only after the card and logs start/stop counts only.
- **Size:** S (agent side).

### N9.10 Lab harness: a simulated phone for every kind
- **What:** a test harness that plays the phone over Matrix events: state, started, result, declined,
  expired and late. It runs every kind in N9.1-N9.8 without a real phone. It also has a lab-phone run list for
  Windy Admin's 7 lab phones, on lab accounts only.
- **Why:** each codon above turns live on this harness. Nothing is tried on Grant's phone or account.
- **Owner:** Windy Agent; Windy Admin (the lab-phone run).
- **Where:** `tests/phone_sim.py` (new), `tests/test_phone_tools*.py`.
- **Contract:** phone-tools v2 fixtures. **Flag:** none (tests).
- **Needs:** N9.1.
- **Done when:** the harness drives each kind through done, declined, expired and outcome-unknown in CI.
- **Size:** M.

### N9.11 HQ sees counts, never content
- **What:** one HQ event per phone request: kind, risk class, outcome (done, declined, expired, unknown,
  refused by policy or limit), duration bucket, platform (iPhone/Android). No names, numbers, text, files or
  screen content.
- **Why:** Grant and Admin need to see that the hands work and how often the card is used, without HQ
  becoming a copy of anyone's phone.
- **Owner:** Windy Agent (emitter); Windy Admin (event family + alarms).
- **Where:** `observability/admin_telemetry.py`.
- **Contract:** Admin's phone-tools event family. **Flag:** the existing ingest env (inert unless set).
- **Needs:** Admin's family name.
- **Done when:** a test shows the event for each outcome with only those keys, and the ingest's content guard
  never 422s it.
- **Size:** S.

---

**Order:** N9.10 → N9.1 → N9.2 → N9.7 → N9.3 → N9.4 → N9.5 → N9.6 → N9.11 → N9.8 → N9.9.
The harness comes first, so every codon has its test from the start. Accessibility and mesh come last, after
the confirm card is proven.

**Weekend (lab only, if Boss picks it once v3 is in):** N9.10, N9.1, N9.2 and N9.7 can be built against the
current contract plus a draft v2 fixture with no phone change. Nothing reaches Zero or Grant's phone without
a GO.
