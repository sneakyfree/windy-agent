# Vault everywhere: a Windy agent that can act like a Claude Code terminal

Grant, 2026-10-26: "I want that everywhere wherever there's an AI agent, as long as that agent has access to the
vaults of Windy Vault." Grant approved the recommendations below the same day ("you have my authority and
permission to proceed accordingly").

## Why a Windy agent doesn't feel like a Claude Code terminal today

A Claude Code terminal feels powerful because it has (1) a general hand: any command, any web API, any file;
(2) the person's keys right there; (3) a strong model that plans long jobs and recovers from errors; (4) the whole
session in mind. A Windy agent today has:

1. **A fixed menu.** Phone-hatched agents run on Windy Chat's agent-roster with 23 hand-built tools (email,
   calendar, sites, web search, agent messaging). Nothing outside the menu is possible: no "call any service".
2. **No Vault on the agents people use.** Windy Vault's agent side exists: capabilities, ~15-min leases decrypted
   only by the agent, ACCESS requests, and SAVE requests (V7.9, built dark). The GitHub and Cloudflare agent
   proxies are declared but not built yet (`/v1/agent/proxy/*` answer 503 "not built yet"). Windy
   Fly has the lease client (#623/#628), switched off and not yet a tool the model can call. The roster has no
   Vault connection at all.
3. **A weak brain by default.** A person who hasn't opted in to outside AI gets gpt-oss:20b
   (MIND_NO_THIRD_PARTY_DEFAULT), even after picking another model in the app (Mind, 2026-10-25).
4. **Short memory.** Roster agents see their last 12 messages.
5. **Deliberate confirm steps** (send, publish). They stay; they protect people.

## The plan (in order of impact)

| # | What | Owner | Builds on |
|---|------|-------|-----------|
| 1 | **"Use my Vault" for the agent**: list what the person shared, ask for access (ACCESS request), offer to save (SAVE request, V7.9 c6) | Windy Agent (Windy Fly first, Zero as the first user); Chat for the roster | agent-routes.v1 (`capabilities`, `requests`, `lease`) |
| 2 | **The person's yes/no screens**: "A helper asks to use ...", "A helper offers to save ..." as a pop-up on phone and desktop, plus an "always save" choice | Windy Hub (confirm screen, V7.9) + Windy Mobile + desktop Word | owner-api approval card |
| 3 | **"Call any service with a Vault key"**: one owner-only tool that makes a web request using a leased key inside the runtime. The model never sees the value: the redactor registers it BEFORE any tool output reaches the model (Vault plan V7.6 c2b), and the lease is released after the call. Design terms: docs/VAULT_CALL_API_DESIGN.md (allowed destinations are owner-set on the Vault item, never in a file the agent can write; sticky taint while untrusted content is in context; writes ask first, DELETE always; pinned resolved IP; full-encoding redactor). Later: a sandboxed code runner | Windy Agent (Windy Fly), then Chat | `lease` + local decrypt (`vault/client.py`) |
| 4 | **The person's model choice sticks**: an easy opt-in to outside AI, and the app says WHY when a pick is switched | Windy Mind + Mobile + Chat (`served_reason` on reply events, proposed 10-25) | `agent_pick.dm_verdict` |
| 5 | **Longer memory** for roster agents | Windy Chat | roster prompt builder |
| 6 | **One runtime, not two**: converge the roster and Windy Fly so each new ability is built once | Hub + Boss decide | - |

Windy Vault's flags (`AGENT_SAVE_ENABLED`, real-secret V1.G) and Vault's own order stay Vault's call.

## Rules that do not change

- A key's VALUE never enters the model's context, a log, a BOARD line or a chat message. Requests are enums only
  (Vault v3.0 KEEP 5, the prompt-injection defence).
- Every new ability ships DARK behind a flag, with tests, Hub OK at the exact head, and a lab run on a synthetic
  owner first. A live flip needs Boss's GO; for Windy Zero, Grant's typed OK too.
- An item the person saves is never granted back to the agent automatically (V7.9 c5).
- "Always save" is an owner opt-in, never the default. Its words, and the yes/no screens' words, go to Grant via
  Boss first (Boss 2026-10-27 00:40Z item 4; principle #10).

## First step (Windy Agent, now)

Windy Fly, dark behind `WINDY_VAULT_TOOLS=1` (with the existing `WINDY_VAULT_LEASE`/`WINDY_VAULT_API_URL`):
owner-band tools `vault_list`, `vault_request_access` and `vault_offer_save` (V7.9 c6), then `call_api` (item 3).
Contract: windy-contracts main 3f30d30 (agent-routes.v1 1.4.0), vendored and hash-pinned. Lab proof on zero-lab
(ET26-TEST passport, synthetic owner from Hub's probe flow) against the Vault SANDBOX (never shipped as a default),
then Zero, each step on its own GO. Shared tool contract for Windy Fly + the roster: Hub writes it in
windy-contracts (Boss 2026-10-27 00:40Z); roster/runtime convergence is decided after Build 138.
