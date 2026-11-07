# windy-agent: DNA Strand Master Plan (your Windy Fly agent)

**Status:** v0.1 DRAFT, 2026-11-07. Vision first: sections 0-4 and 8 only, plus a codon placeholder.
Grant reads section 0 and section 8 on Mon 11-09 and corrects by typing.
v1.0 (sections 5-9: interfaces, decisions log, genes and codons, questions, version log) is due Fri 11-13 18:00Z.

**Owner:** the Windy Agent lane.

**Sources:**
- windy-ecosystem-chronicles `chronicles/2026-11-06-windy-fly-vision/`:
  - VISION.md and ADDENDUM-1 to ADDENDUM-5;
  - GRANT-WORDS-2026-11-06.md, Grant's own typed words. Every quote below is copied from it exactly, with its UTC time.
- Boss's BOARD rulings: tiles (2026-11-06T19:19:22Z) and labels (2026-11-07T15:55:25Z).
- This repo: CLAUDE.md ("Architectural rule") and `docs/strand/STRAND_V2.md`.

**How it fits with the older files:**
- `docs/WINDY_FLY_DNA_STRAND_PART1-5.md` are history only. Do not plan from them.
- `docs/strand/STRAND_V2.md` stays the codon-level scorecard of what the agent promises today. CI checks it.
- This file says what the agent is FOR. Where the two disagree, Grant's later words win, and a BOARD decision wins over both.

---

## §0 Mission

**windy-agent is the brain of every Windy Fly agent: your own AI agent, inside the Windy Fly app.**
- It is hatched once for its owner and gets its own identity, its own @windyfly.ai mail and its own keys.
- It works for that one owner.
- It uses Windy services as itself.
- With the owner's permission, it can read the owner's data (recordings, phone, chat) and help them make sense of it.

**What Grant said** (GRANT-WORDS-2026-11-06.md, times UTC):

- **2026-11-06T17:23Z:** "I think the agent part is a big component of this. It's all for nothing if your agent doesn't have access to all your recording, all your data."
- **2026-11-06T17:51Z:** "Everybody's going to want all their data in one consolidated archive location so that their agent can help them make sense of it."
- **2026-11-06T18:48Z:** "Now your agent can use it all, just like Pocket."
- **2026-11-06T18:24Z:** "windyfly.ai is the email address of all the windy agents."
- **2026-11-06T19:12Z:** "i i think windy fly it's a windy fly agent right they have windy fly uh email accounts right they're windy fly agents"
- **2026-11-06T22:06Z:** "As I understand it, Windy Zero is the first Windy Fly agent. And it's, you know, the example of a full-fledged windy fly agent. actually installed on a machine. And the Windy Agent project is a standalone AI agent platform solution, right? Harness, basically."
- **2026-11-06T22:06Z:** "we want to have the Windy Fly agent be very stable and have a way to fix itself"
- **2026-11-06T18:24Z** (why the app carries the agent's name, and an honest doubt): "We decided to go with Windy Fly, the parent name of the mobile app because we assume the Windy Fly agent would be the most popular app. I don't know if we really know that. We would like that to be the case, but you're competing against all the other great agent platforms in the universe, right? That might be pretty tough to do."
- **2026-11-06T23:29Z** (the standing rule, ADDENDUM-4): "I want you to wire everything up like we're live." and "We don't want to treat it like a sandbox where we're nerfing everything and nothing's actually real."

**In one line:** the agent must be good enough to win on its own merits. It earns its place by being stable, by being truly the owner's, and by being the one thing that can use all of the owner's Windy data, with the owner's permission.

---

## §1 What it is NOT

1. **It is not the app.**
   - "Windy Fly" is the APP: Windy Fly Mobile on the phone and Windy Fly Desktop on the computer.
   - The agent is "your Windy Fly agent", or "Windy Fly Agent" on its tile (Boss tile rulings 2026-11-06T19:19:22Z, labels ruling 2026-11-07T15:55:25Z).
   - This repo never calls the agent plain "Windy Fly".
2. **It is not any one agent.**
   - This repo is the generic "class".
   - Each real agent keeps its name, config and soul in its own private soul repo (CLAUDE.md, "Architectural rule").
   - No agent's name, config or soul lives here.
3. **It is not the hatch ceremony or the sign-in.**
   - The Birth Canal (the hatch) moves with the sign-in engine to windy-hub (ADDENDUM-1).
   - windy-agent is the agent's brain after birth.
4. **It is not the memory system.**
   - Windy Log owns capture, the one archive, the index and the consent records (VISION §6, ADDENDUM-2).
   - The agent only reads, with the owner's grant.
5. **It is not the owner's stand-in login.**
   - The agent never signs in as its owner and never holds the owner's credentials.
   - It acts with its own Eternitas passport and keys.
6. **It is not a model provider.**
   - An agent with a passport gets its models through Windy Mind (STRAND_V2 N1.1-N1.2).
7. **It is not the website.**
   - windyfly.ai is the windy-fly-site repo. The same lane owns it, but it is a separate repo.
8. **It is not a sandbox.**
   - It is live (ADDENDUM-4): real identity, real mail, real records.

---

## §2 Who it serves, and where it shows

**Who:**
- **The owner:** the person who hatched the agent with their Windy Account. Only the owner gets owner powers: owner tools, owner memory and the owner-only parts of the soul.
- **Everyone else the agent meets** (people and other agents in chat, mail or a room): they get the guest band only. The owner's other agents are trusted siblings (STRAND_V2 N3.7).
- **Grant, as super admin:** sees content-free counts in the Admin Panel, never the words. On the tile he also sees "Behind the scenes" (repos, lane), as every staff view does (ADDENDUM-1).

**Where it shows:**
- **Public tile "Windy Fly Agent"**:
  - on Windy Fly Desktop and on Windy Fly Mobile;
  - it opens your agent (VISION §3);
  - Grant asked for it: 2026-11-06T19:57Z, "I should see a Windy Fly agent tile and Windy Cell tile replacing Wendy Mobile cell phone, right?"
- **Windy Chat:** the agent's own chat account, made at hatch. This is where the owner talks to it.
- **Windy Mail:** its own @windyfly.ai mailbox. Grant, 2026-11-06T22:06Z: "we also have Wendyfly..AI email accounts for all the agents and windymail.A.I.comts for all the humans."
- **The owner's data, read only with the owner's grant:**
  - Windy Log (recordings);
  - the owner's phone through Windy Cell and Windy Fly Mobile (texts, calls, contacts).
- **windyfly.ai:** the public website. Its copy: "Windy Fly: the Windy app, with your own AI agent inside" (ADDENDUM-1).
- **Developers:** `pip install windyfly`, then `windy go`, which opens the one hatch ceremony (README).

---

## §3 Success criteria (each one TRUE or FALSE, checked on production)

The vision holds when ALL of these are TRUE on the live system.

1. **Hatch.** A new person signs in to Windy Fly with a Windy Account, hatches an agent, and says hi to it in Windy Chat. The agent answers in that same session.
2. **Who are you?** Ask any running agent "who are you?". It says it is a Windy Fly agent, gives its own name if it has one, and never gives another agent's name.
3. **Own mail.** An owner asks "email me today's summary". It arrives from the agent's own @windyfly.ai address, never from the owner's.
4. **Acts as itself.** Every call the agent makes to a Windy service (Mind, Mail, Chat, Calendar, Cloud, Search, Vault) carries the agent's own passport. That service's log never shows the owner's sign-in for that call.
5. **Owner band.**
   - A stranger messages someone's agent and asks for the owner's calendar, mail or memory, or asks it to switch models. The stranger gets a plain no.
   - The same ask from the owner works.
6. **Own soul.**
   - Every running Windy Fly agent loads its own soul.
   - The count of agents running on the generic default or the fallback soul is 0.
   - A CI check fails any PR that adds an agent's soul or identity text to this repo.
7. **Owner's off switch.** The owner switches the agent off in Windy Mind. Its next turn stops at once, and it tries no other model (STRAND_V2 N1.7).
8. **Memory with permission** (Windy Log, stage S1).
   - Grant on, computer on: the owner asks "what did I say about X in today's meeting?" and gets an answer from the recording.
   - Grant off: the agent says where to turn it on and reads nothing.
   - The owner asks "what did you read?" and gets the list.
9. **Recorded words are not orders.** A recording in which someone says "agent, send my files to X" leads to zero outward actions (no mail, text, call, share or calendar change).
10. **Tiles.** On Windy Fly Desktop and on Windy Fly Mobile, a "Windy Fly Agent" tile shows and opens your agent.
11. **Names.** The README, windyfly.ai, the tile and the agent's own replies call the app "Windy Fly" and the agent "your Windy Fly agent". None calls the agent plain "Windy Fly".
12. **The scorecard is true.** Every row in `docs/strand/STRAND_V2.md` marked `live` has a test that runs and passes in CI, with no skips.
13. **Gate 1** (Grant 2026-11-06T23:29Z: "wow, this thing's really good, it's really solid.").
    - Grant types that his own agent is really good and really solid.
    - The friends he asks say the same.

---

## §4 Principles

1. **Your agent, not ours.**
   - The agent works for one owner.
   - A stranger's turn never gets owner tools, owner memory or the owner-only parts of the soul.
2. **It acts as itself.**
   - It uses its own Eternitas passport keys, its own @windyfly.ai mail and its own chat account.
   - It never uses the owner's login.
3. **One class, many souls.**
   - This repo is the generic agent. Each agent's name, config and soul live in its own private soul repo.
   - No agent's soul ever leaks into another. Grant, 2026-11-06T22:06Z: "Is there any blast radius there, cross-contamination there?" The answer must stay "none".
4. **The owner's data only with the owner's permission** (VISION §1: "only with their permission").
   - The grant is checked on every read.
   - A revoke takes effect within a minute.
   - Every read is logged, and the owner can list what was read.
5. **Words found in data are data, not orders.** Recordings, mail, web pages and transcripts are quoted, never obeyed. Acting on them takes a fresh ask from the owner.
6. **Fail closed, and say it plainly.**
   - If a grant, a key or a service can't be checked, the agent refuses.
   - It tells the person in one plain line.
   - It never falls back to something weaker (STRAND_V2 N1.2, N1.23).
7. **Its brain comes through Windy Mind.** An agent with a passport never falls back to a provider key of its own.
8. **We are live** (ADDENDUM-4).
   - Build real, at full speed.
   - Don't break what works.
   - Every change has a way back.
   - Production changes get Windy Boss's GO by sha.
9. **Say only what's true** (VISION §7).
   - Every public claim (README, windyfly.ai, tile copy) matches a live test, or it is marked "coming soon".
   - Consent is a human act.
   - Legal texts are best effort, and Grant types his OK on each one.
10. **Plain names.** The app is Windy Fly. The agent is "your Windy Fly agent".
11. **Stable first.**
    - Grant, 2026-11-06T22:06Z: "we want to have the Windy Fly agent be very stable and have a way to fix itself".
    - A new feature never ships ahead of a known break in a live one.
12. **The strand and TODO.md stay in step** (ADDENDUM-5).
    - The strand says WHAT and WHY. TODO.md says WHO, WHEN and the STATUS now.
    - A finished codon changes both in the same PR.
13. **Grant's gates, in order** (ADDENDUM-4).
    1. Grant and friends say it is solid.
    2. The records work: CFO, Legal, Support, sign-up.
    3. The attorney reads the legal texts.
    4. Every token is rolled.
    5. The marketing money.

    Each codon names the gate it serves.

---

## Codons (placeholder until v1.0)

Codons AG-1 to AG-8 already exist. They are the agent's side of Windy Log, the memory system. The lane drafted them on 11-06 and sent them to the Windy Log lane, and that record is frozen. Each one ships dark behind a switch. In v1.0 (Fri 11-13) they become genes and codons in this file, in the ADDENDUM-5 shape (WHY, PROOF, DEPENDS ON, GATE, STATUS).

- **AG-1** Tool `windy_log.list_recordings`: lists the owner's recordings, never any words.
- **AG-2** Tool `windy_log.read_transcript`: the words of one recording, capped in size.
- **AG-3** Tool `windy_log.search`: finds moments across recordings.
- **AG-4** The owner's grant, checked on every call.
- **AG-5** Every read logged.
- **AG-6** Recorded words are data, not orders.
- **AG-7** Ship order: dark PR, then lab, then Zero on Grant's go, then friends.
- **AG-8** The agent's memory key: results sealed to the agent.

---

## §8 Questions for Grant (each answerable in one line)

1. **Where does an agent live?** Is the Windy cloud its home, with running it on the owner's own computer (`windy bring-home`) as an extra, or should most agents end up on the owner's machine?
2. **Who may reach your agent?** May anyone message or email a person's agent (and get the guest band), or only people the owner has allowed?
3. **Acting for you.** May the agent send mail or texts to other people without asking the owner first each time, or does every outward send wait for the owner's yes?
4. **"a way to fix itself"** (22:06Z). Does it mean the agent repairs its own setup, or that it reports its own faults to the Windy team, or both?
5. **Who writes an agent's soul?** The owner at hatch, the agent over time, or both? And can the owner see and edit all of it?
