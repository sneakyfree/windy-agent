# One agent runtime for everyone: windyfly and the Chat agent-roster (convergence plan)

**Status:** DRAFT for Boss's decision. Due Tue 2026-11-03 18:00Z.
**Authors:** Windy Agent (this skeleton, §2a and the windyfly side of §3) and Windy Chat (§2b and the roster side of §3; second reader).
**Scope:** a PLAN only. No code moves in this sprint (Boss 2026-11-02 11:06Z). Facts are marked with their source; "unknown" is said plainly.

## 1. The decision Boss is asked to make
Today two different programs answer as "your Windy agent":
- **windyfly** (repo windy-agent) runs ONLY Grant's own agent, Zero, on Windy 0.
- **Chat's agent-roster** (windy-chat services/agent-roster, on Kit 0) runs every agent people hatch, including lab agents: 57 on 2026-11-02 (Chat, /status counts only).

They have different tools, memory, words and auth, so every product promise has to be built, checked and worded twice. P136-5 (10-28/29) showed it: each runtime needed its own true sentence about calendars.
The choice: **(A)** move hatched people onto windyfly; **(B)** move Zero onto the roster; **(C)** keep both and converge on shared contracts only. §5 compares them; §6 is the recommended order.

## 2. Today's two paths

### 2a. windyfly (Windy Agent; facts from windy-agent master 393f9811, 2026-11-02)
- **Runs:** one Python process per channel (Zero: windy-0@telegram, windy-0@matrix), its own state dir and SQLite (`windy-0.db`), per-install agent key. Hosting = the owner's machine (Windy 0 for Zero; any box via `pip install windyfly` 0.7.6 / `windy go`).
- **Channels in code:** Telegram, Matrix, email, SMS (+inbox), Slack, Discord, Teams, WhatsApp, Signal, IRC. Zero uses Telegram + Matrix.
- **Brain:** Windy Mind (EPT+agent aud windy-mind + DPoP), with an auto model, a lifeboat local model and BYOM via Mind.
- **Memory:** durable and per agent: episodes, nodes/graph, goals, journal, skills, decay/forget, retention, snapshots, cost ledger (src/windyfly/memory/*). It survives restarts and is searchable by the agent (memory_search).
- **Tools (capability registry + tool registry):** Windy Calendar (book/block/create_meeting; add_event dark), Windy Mail (owner-confirm for new recipients), SMS, phone call (make_call, dark), Windy Cloud files, Windy Sites/Code builder, Windy Vault (list/request/offer-save; call_api dark), web search, weather/news/todos/reminders, collaborators/teams, GitHub, shell/fs/ssh/fleet (owner band, gated), MCP client, vision, skill learning, auto-repair.
- **Safety model:** trust bands (OWNER/TRUSTED/USER/SANDBOX), I2 taint (no outward tools after untrusted content), audit ledger, owner-confirm cards, strict args.
- **Identity/auth:** Eternitas passport + agent key; mode B (EPT+agent + DPoP) to Mind, Vault, Text, Cloud; the legacy 365-day EPT is being retired (windy-agent stop 2026-12-15).
- **Telemetry:** service.boot/health every 5 min (channel, mind_legacy_auth), llm.call per turn; HQ alarms.
- **Spec:** docs/strand/STRAND_V2.md, 579 live / 91 retired / 14 planned codons, CI-enforced.

### 2b. Chat agent-roster (Windy Chat; facts from Chat 2026-11-02; TO BE COMPLETED BY CHAT)
- **Runs:** one service on Kit 0 serving every hatched agent (57 now, incl. lab owners' agents; the real-person split comes from Chat's counts-only query).
- **Memory:** NO store of its own. Conversation memory = the Matrix room: each turn pulls the last ~12 m.room.message events and gives the model the last 10. Nothing is summarised or kept between turns outside Matrix. Small per-agent preferences live in Matrix account data (model choice, peer policy, per-room stop). Longer-term memory on the Mind path is Mind's store. Reads onboarding.db read-only for agents, settings and team.
- **Tools:** [Chat: tools.js inventory: calendar_* (7), ...]
- **Channels:** [Chat: Matrix DM only? others?]
- **Auth to services:** [Chat: per-agent EPT+agent? a shared service token?]
- **Hosting/cost/isolation:** [Chat: one process for all agents? per-agent isolation? cost per agent?]

## 3. What a hatched person gets vs loses (each way)
| Area | Roster today | windyfly today | Who's ahead |
|---|---|---|---|
| Long-term memory | last 10 room messages; prefs in account data | durable per-agent memory, search, journal, goals | windyfly |
| Calendar | 7 tools [Chat] | book/block/create_meeting (+add_event dark) | [compare] |
| Mail / SMS / calls | [Chat] | mail with owner-confirm, SMS, make_call (dark) | [compare] |
| Vault | [Chat] | list/request/offer-save (+call_api dark) | [compare] |
| Channels | [Chat] | 10 channel adapters; Zero uses 2 | windyfly (breadth) |
| Runs where | Kit 0, shared, always on | the owner's machine (Zero: Windy 0) | roster (no install) |
| Ops cost per person | [Chat] | one process set per agent | roster (likely) |
| Safety | [Chat: bands? taint? audit?] | bands, taint, audit, confirm cards | [compare] |

## 4. Migration steps (per option)
- **(A) hatched -> windyfly:** run windyfly multi-tenant on Kit 0 (one process per agent or a pooled worker), memory store per agent, and a Matrix identity handover (windyfly already takes the session via chat_session; the roster yields while windyfly holds the runtime claim). [sizes TBD]
- **(B) Zero -> roster:** port Zero's durable memory and tools into the roster; this loses windyfly's memory model unless the roster gains one. [Chat]
- **(C) shared contracts only:** keep both, and make every tool and word come from ONE contract (as vault-tools.v1 already does), with conformance tests in both runtimes. Lowest risk, but permanent double work.

## 5. Risks for real people
- Memory loss in any move: what the person's agent "knows" must carry over or be told plainly.
- Words drift between runtimes (P136-5): a person hears different truths from two agents.
- Auth: the legacy EPT sunset (12-03 Mind, 01-15 Eternitas) must not be bundled with a runtime move.
- Downtime and identity: one Matrix identity, one voice (runtime claim), with no double replies during a handover.

## 6. Recommended order and size (DRAFT; Agent's view, Chat to counter)
1. **Now (S):** shared contracts for every overlapping tool (calendar, mail, vault), with conformance tests in both runtimes. That stops P136-5-style drift whichever option wins.
2. **Measure (S):** per-person cost and quality of each runtime on lab agents (counts only).
3. **Decide A/B/C** on that data. [size of A/B: Chat + Agent, after §2b]
No step touches real people's data or runtimes without its own Boss GO.
