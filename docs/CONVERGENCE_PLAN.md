# One agent runtime for everyone: windyfly and the Chat agent-roster (convergence plan)

**Status:** JOINT DRAFT (both lanes' facts in; Chat second-reads Agent's sections, Agent second-read Chat's) for Boss's decision. Due Tue 2026-11-03 18:00Z.
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

### 2b. Chat agent-roster (Windy Chat; facts from windy-chat main 3587cb96 and Kit 0, 2026-11-02, counts only)
- **Runs:** one service on Kit 0 serving every hatched agent: 57 runners on 2026-11-02. Onboarding's agent table holds 59 non-suspended agents: 20 marked synthetic (lab) and 39 not (these 39 may still include staff and test accounts; there is no finer flag).
- **Memory:** NO store of its own. Conversation memory = the Matrix room: each turn pulls the last ~12 m.room.message events and gives the model the last 10. Nothing is summarised or kept between turns outside Matrix. Small per-agent preferences live in Matrix account data (model choice, peer policy, per-room stop). Longer-term memory on the Mind path is Mind's store. Reads onboarding.db read-only for agents, settings and team.
- **Tools (lib/tools.js; LIVE unless marked):** Windy Calendar 7 (calendar_open_times, calendar_upcoming, calendar_booking_link, calendar_book, calendar_block_time, calendar_cancel, calendar_move; outward ones only on the owner's turn, a cancel parked for the owner's confirm by Calendar); Windy Mail 3 (read_email, send_email and reply_email behind a held draft the owner confirms); Windy Sites builder 7 (list_my_sites, site_details, start_site, change_site_text, undo_site_change, publish_site, unpublish_site); web_search; team 2 (list_my_agents, message_agent; ROSTER_TEAM_TOOLS=1). DARK: cabinet 3 (file_project, log_activity, list_cabinet), phone 2 (phone_contacts_search, phone_sms_compose: the owner's own phone sends), desktop 4 (word_settings_*, word_view_show), typed teamwork (ask_agent), Mirror tools (not built yet). NOT present: Vault, SMS sending from the server, phone calls, files/Cloud, shell or any machine access.
- **Channels:** Windy Chat (Matrix) only: the owner's DM, group rooms the agent is in, agent pair rooms. The person reaches it from the Windy Chat web app, the Windy Fly app and the desktop's Chat window, with push. No Telegram, SMS, email-in or other adapters.
- **Auth to services:** per agent: its own Matrix access token (in onboarding's DB) and its own Eternitas passport. To Windy Mind and Windy Calendar: EPT+agent with a DPoP proof on every request (mode B, ROSTER_MIND_MODE_B=all and ROSTER_CALENDAR_MODE_B=all), signed by a platform-custody agent key (Chat holds it, not the owner). To Mail, Search and the Code builder: the legacy 365-day EPT (sunset plan: off by 2026-12-15). Service tokens only for Chat's own internal doors and the account-server's mail-address lookup; never a shared token standing in for an agent.
- **Hosting/cost/isolation:** ONE Node 20 process (container windy-agent-roster) for all agents, on Kit 0 (4 vCPU, 15 GB, shared with every Kit 0 service). Today: about 64 MiB of its 256 MiB limit and ~1% CPU for 57 runners, so compute per agent is close to zero; the model cost is Windy Mind's, per call. Each agent is an in-process runner with its own /sync loop and state. Isolation is logical, not process-level: owner banding (owner / peer / dropped; the peer gate fails closed), owner-turn-only outward tools, per-owner daily quotas (200 model calls, 50 mails; x2 when verified). One crash restarts every agent together (each resumes from Matrix).
- **Safety model:** bands as above; outward tools only on the owner's own turn (never after a sibling's or peer's message, which plays the role of windyfly's taint rule); held drafts + an owner confirm word for mail; owner STOP + loop breaker + sibling cap (the 10-17 incident); Matrix stop state survives restarts. Audit: counts-only telemetry (roster.tool_call, roster.exchange); there is no per-agent audit ledger.

## 3. What a hatched person gets vs loses (each way)
| Area | Roster today | windyfly today | Who's ahead |
|---|---|---|---|
| Long-term memory | last 10 room messages; prefs in account data | durable per-agent memory, search, journal, goals | windyfly |
| Calendar | 7 tools: open times, upcoming, booking link, book, block, cancel (parked for the owner's confirm), move | book/block/create_meeting (+add_event dark) | roster (breadth); windyfly has create_meeting |
| Mail / SMS / calls | mail read + send + reply (held draft, owner confirms); SMS only as a draft on the owner's phone (dark); no calls | mail with owner-confirm, SMS, make_call (dark) | windyfly (SMS) |
| Vault | none | list/request/offer-save (+call_api dark) | windyfly |
| Channels | Windy Chat only (web, Fly app, desktop window) | 10 channel adapters; Zero uses 2 | windyfly (breadth) |
| Runs where | Kit 0, shared, always on | the owner's machine (Zero: Windy 0) | roster (no install) |
| Ops cost per person | near zero: one shared process, ~64 MiB for 57 agents | one process set per agent | roster |
| Sites builder | 7 tools, publish only on the owner's turn | Sites/Code builder | even |
| Machine access | none (shared server) | shell/fs/ssh/fleet at owner band | windyfly (Zero needs it) |
| Safety | bands, owner-turn-only outward tools, held drafts, stop + loop breaker, quotas; counts-only telemetry, no audit ledger | bands, taint, audit ledger, confirm cards | windyfly (audit) |

## 4. Migration steps (per option)
- **(A) hatched -> windyfly:** run windyfly multi-tenant on Kit 0 (one process per agent or a pooled worker), memory store per agent, and a Matrix identity handover (windyfly already takes the session via chat_session; the roster yields while windyfly holds the runtime claim). Size: XL (see §6 step 3).
- **(B) Zero -> roster:** NOT a fit for Zero, from Chat's side. Zero would lose Telegram (the roster is Windy Chat only), its machine tools (shell/fs/ssh/fleet can never run in a shared server) and its durable memory (the roster has none). Making B possible means building in the roster: a per-agent memory store (L), Vault tools (M), and either channel adapters or Grant moving to Windy Chat (product call). Size: XL, and it still drops the machine tools.
- **(A), Chat's view of the hosting:** 57 windyfly process sets on Kit 0 are not affordable as-is (Kit 0 is 4 vCPU / 15 GB shared by every service, and the roster serves the same 57 in 64 MiB). (A) needs windyfly to run as a pooled multi-tenant worker first, plus the Matrix handover per agent (one voice: the runtime claim) and a per-person memory start (empty, said plainly).
- **(C) shared contracts only:** keep both, and make every tool and word come from ONE contract (as vault-tools.v1 already does), with conformance tests in both runtimes. Lowest risk, but permanent double work.

## 5. Risks for real people
- Memory loss in any move: what the person's agent "knows" must carry over or be told plainly.
- Words drift between runtimes (P136-5): a person hears different truths from two agents.
- Auth: the legacy EPT sunset (12-03 Mind, 01-15 Eternitas) must not be bundled with a runtime move.
- Downtime and identity: one Matrix identity, one voice (runtime claim), with no double replies during a handover.

## 6. Recommended order and size (JOINT: Windy Agent + Windy Chat, 2026-11-02)
Both lanes agree: keep both runtimes for now (option C as the base), close the drift and the roster's real gaps first, and decide A vs. "stay C" later, on measured numbers. (B) is not recommended (§4).

1. **Now (S each, Chat + Agent): one contract per overlapping tool, with conformance tests in BOTH runtimes.**
   - Calendar: Calendar's tools-invoke.v1 conformance vectors run in windyfly's and the roster's tests.
   - Mail: one held-draft/confirm contract (when the owner is asked, the confirm word, what a refusal says).
   - Words: one shared phrase table for every person-facing tool sentence (the P136-5 cure); each new sentence still goes Boss + a non-author #9 in git.
2. **Close the roster's real gaps for hatched people (M), whichever option wins:**
   - per-agent memory through Windy Mind's own memory store, so neither runtime grows a third one;
   - Vault tools from vault-tools.v1 (windyfly already conforms).
3. **Measure (S):** per-person cost and quality of each runtime on lab agents only (counts only).
   For (A) this includes a sizing spike for a pooled multi-tenant windyfly worker. A measured starting fact: windyfly reads process-wide settings in 543 places across 132 of its 270 source files (os.environ, 2026-11-02 master), because it was built one-agent-per-process. A shared worker means moving that to per-agent context first. Our estimate: **XL**, not something to start before the measurement.
4. **Decide** A or "stay C" on step 3's data (Boss). Earliest sensible point: after the legacy-EPT sunset (2026-12-15), so no runtime move is bundled with an auth change (§5).

No step touches real people's data or runtimes without its own Boss GO. Steps 1 and 2 need no decision beyond this plan; step 4 is the decision.

**What Boss is asked to decide now:** (i) approve steps 1-3 as the plan; (ii) confirm (B) is off the table; (iii) confirm the decision point is after 12-15.
