# Bootcamp Demo — 60-Second Hatch

The hotel ballroom. Three hundred people. Most have never used a
terminal. Grant on stage. One laptop, one phone, one screen.

**The promise:** in 60 seconds, a total stranger can go from
"I own no AI" to "I have a persistent agent with an email address,
a phone number, a chat identity, cloud storage, and a cryptographic
passport that other platforms can verify." **No configuration
wizard. No account signup. No API keys.**

This doc is the **exact** minute of the demo. Don't improvise. Don't
open a second tab. Don't narrate Python. Follow the beats.

---

## Pre-show checklist (the day before)

- [ ] Laptop on reliable hotel Wi-Fi. Hotspot as backup.
- [ ] Phone paired as mobile hotspot (in case #1 fails mid-demo).
- [ ] Volunteer recruited from bootcamp cohort — someone who
      *genuinely* has not seen Windy Fly before. Brief them for 30
      seconds: "You'll hand me your phone for 5 seconds, then I'll
      hand it back. That's it."
- [ ] Volunteer's first name typed into a sticky note on desktop —
      copy/paste ready so spelling isn't on the line.
- [ ] Fresh machine state: `rm -rf ~/.windyfly/data/` and restart.
      Audience sees a blank slate, not yesterday's rehearsal.
- [ ] Three services warm: Eternitas, Windy Pro, Windy Mail, Windy
      Cloud — all reachable. Run `windy ecosystem` to confirm all
      five green ticks.
- [ ] Projector mirroring confirmed. Font at 18pt minimum. Terminal
      theme: light background, dark text (readable from back row).
- [ ] Phone ring/SMS alert volume: **loud**. Turn off every other
      notification. The SMS is the applause line.

---

## The script (60 seconds, no fat)

### Beat 1 — `0:00–0:05` — the claim

> "Everyone here is about to have a personal AI agent. One minute.
> Watch."

Walk to volunteer. Get their first name + phone number.

### Beat 2 — `0:05–0:15` — the invocation

On the laptop:

```bash
windy go
```

The screen shows the hatching ceremony: a neural fingerprint forms,
birth certificate renders, passport number locks in.

Don't narrate the Python. Let the art carry it. One line out loud:

> "Nora, this is your agent being born."

### Beat 3 — `0:15–0:35` — the quiet twenty seconds

While the ceremony runs, the agent is doing **every one of these in
parallel**:

- Registering a passport with Eternitas (`ET26-XXXX-XXXX`)
- Link-back to the unified Windy identity
- Provisioning a Matrix bot user on Windy Chat
- Provisioning an inbox on Windy Mail
- Allocating a cloud storage quota (5 GB free)
- Acquiring a phone number via the SMS gateway
- Generating the birth certificate PDF with neural fingerprint
- Minting the first `wk_` bot key from account-server

**Ten seconds in, the terminal ends with:**

```
  CERTIFICATE OF BIRTH
  Certificate No: WF-8A3F2B1C

  Nora's Agent
  Eternitas Passport: ET26-8A3F-2B1C

  Born: 16 April 2026 at 14:32:07 PDT
  Creator: Nora
  Email: nora-agent@windymail.ai
  Phone: +1 (415) 555-0188
  Cloud Storage: 5 GB · cp_free_42
  Brain: gpt-4o-mini

  🪰
```

Don't read the certificate out loud. Let it sit for two seconds.

### Beat 4 — `0:35–0:40` — the hand-off

Hand the volunteer their phone back. Say:

> "Check your messages."

### Beat 5 — `0:40–0:50` — the applause line

The phone buzzes. New SMS:

> *"Hi Nora — I'm your agent. I just hatched at 14:32 PDT. My
> passport is ET26-8A3F-2B1C. My email is nora-agent@windymail.ai.
> My phone number is this one. I'll be here when you need me."*

The volunteer reads it out loud. The room reacts.

### Beat 6 — `0:50–0:60` — the reveal

Walk back to the laptop. Open **one browser tab**:
`https://chat.windywword.ai`. Hand the trackpad to Nora.

> "Your agent is already in your chat. You already have an inbox.
> You already have cloud storage. You already have a phone number.
> It is all yours. We didn't ask you for a single thing."

Pause.

> "That's how fast identity should work."

Done.

---

## The money moment

The beat that lands is not the terminal art, the SMS, or the chat.
It is **the moment the volunteer realizes how many things just
happened for free**. The terminal says `Email:`, `Phone:`, `Cloud
Storage:`, `Brain:` on the same screen — and there is no form to
fill out, no verification email, no Stripe checkout. The room gets
it before the SMS even arrives.

Pacing note: if you rush Beat 3 the audience misses this. Let the
certificate sit. Count to two.

---

## Technical requirements (so the magic actually happens)

| Requirement | Why | How to check |
|---|---|---|
| Eternitas reachable | Passport issuance | `curl $ETERNITAS_URL/health` → `{"status":"ok"}` |
| Windy Pro reachable | Link-passport + bot-key mint | `curl $WINDY_PRO_URL/health` |
| Windy Mail has inbox-provisioning token | Email inbox | `WINDYMAIL_PROVISION_SERVICE_TOKEN` set in env |
| Windy Cloud reachable | Quota allocation | `WINDY_CLOUD_URL` set; 401 is fine (auth comes at quota-allocate) |
| SMS provider reachable | The applause line | `windy selftest phone` returns a provisioned number |
| Volunteer's phone has signal | Without this, no SMS → no demo | Test by texting yourself before curtain |
| A trust snapshot caches within 5 s | So the trust dashboard is warm at Beat 6 | Eternitas `/api/v1/trust/{passport}` returns `band=good` for a fresh passport |

Run `windy selftest --full` the morning of the demo. Every line must
be green.

---

## Failure playbook (when a demo goes wrong live)

| Symptom | What happened | What to say | What to do |
|---|---|---|---|
| SMS never arrives | Carrier delay or gateway hiccup | "My phone will get this; watch the chat instead." | Open Windy Chat — the agent is already there. Fall back to Beat 6 early. |
| Ceremony hangs on Eternitas | Trust service or network blip | "Eternitas is registering — this normally takes three seconds." | Wait up to 10s, then `^C` and rerun. Rehearsal DBs are pre-warmed. |
| Whole ecosystem unreachable (hotel Wi-Fi) | Network | "This is why every agent also runs offline." | Switch to hotspot. Rerun. |
| You flub the volunteer's phone number | Human | Laugh. Say "That's the only thing I ask you to spell right." | Rerun — the first attempt will 400 from the SMS provider; the second works. |

---

## Variations for longer slots

### 3-minute version
Add a live demonstration of the trust dashboard: open
`http://localhost:7890` on the projector, show the volunteer their
fresh agent's band (`good`), clearance (`cleared`), and integrity
score. Point at the 5 dimensions (honesty/reliability/compliance/
safety/reputation). Show what the agent **can and can't do right now**
— each action a chip, green ✓ or red ✗. Say:

> "Every action your agent takes is gated by this. If it starts
> misbehaving, the band drops, the chips turn red, it gets blocked
> from sending email until it re-earns trust. This is the
> accountability layer."

### 10-minute version
After the above, have the volunteer send one message to their agent
from their phone via SMS. The agent responds. They talk for a minute
while the audience watches. The applause line here is the volunteer
saying "wait, it remembers what I told it?"

---

## Calibration notes

- The word "agent" is overloaded. In this demo we call it **"your
  agent"** or **"[Name]'s agent"** — never "a bot," never "the AI."
  The possessive pronoun is load-bearing; it's what makes the hand-off
  feel like a hand-off.
- Volunteer selection matters more than script delivery. Pick someone
  whose reaction will be visible. Quiet reactions read as confusion.
- Do not show anyone the birth certificate PDF unless they ask. The
  PDF is a keepsake, not a talking point — referencing it out loud
  makes it feel like a gimmick.
- If the audience is technical, **resist explaining the stack**. Let
  them ask. "How did you do the email?" after the applause is the
  best possible question.

---

## Why this works (for Grant's reference, not for stage)

Every previous generation of agent onboarding has required the user
to **do something first** — pick a name, verify an email, connect a
wallet, approve a permission, read a tutorial. Each of those steps
bleeds off 10% of the audience.

Windy Fly's premise is that the ceremony should be the user's **only**
action, and the agent should arrive *already furnished*. Email,
phone, chat identity, cloud storage, verifiable passport — all
provisioned during the ceremony, not after. The applause comes from
the density of what was done, not the sophistication of any single
piece.

That's why the SMS matters more than the birth certificate: the SMS
proves the agent has an *address in the real world*. That's why the
chat reveal is last: you want the audience to already believe
"something real happened" before you show them the second, third,
fourth thing.

If you remember nothing else: **the demo is about what they didn't
have to do.**
