# WhatsApp gateway: design

Status: **design kept, code parked.** A first working gateway (about 16 KB of Python: webhook, pairing, Matrix bridge,
outbound send) was built on 2026-07-03 in a separate private repository, now archived. That repository keeps the
code and can be brought back. This page keeps the DESIGN in the public agent repo so it isn't lost (Boss repo review,
BOARD 2026-11-06T22:10:16Z; Windy Agent's answer 22:11:33Z). Nothing here is live.

## The problem

WhatsApp delivers an incoming message by calling a webhook on a **public URL**. A Windy Fly agent usually runs on its
owner's own machine, behind a home router, with no public URL. So an agent can't receive WhatsApp directly, and
asking every owner to expose one would be unsafe and hard.

## The design: one public gateway, relayed through Windy Chat

Windy runs ONE public gateway that owns the WhatsApp number. It relays each message to the right agent **through
Windy Chat (Matrix)**, which already reaches agents behind a home router. The agent needs no WhatsApp code at all: to
the agent, a WhatsApp conversation is just one more Windy Chat room.

```
 person on WhatsApp ─▶ messaging provider ─▶ gateway /whatsapp/webhook ─▶ find the paired agent
                                                                        ─▶ post into that agent's portal room
 person on WhatsApp ◀─ messaging provider ◀─ gateway relays the reply  ◀─ the agent answers in the room
```

## Decisions made in July (kept)

| Decision | Choice | Why |
|---|---|---|
| Provider | a WhatsApp Business provider with a webhook (Twilio in the prototype) | one integration, a sandbox for testing |
| Numbers | ONE shared Windy number, plus per-person PAIRING | per-agent numbers need per-number business approval; that is a later scale step |
| Transport to the agent | a Matrix bridge bot in Windy Chat | reaches agents behind a home router; reuses Chat's rooms, encryption and auth |

## Parts

| Part | Role |
|---|---|
| Settings | everything from the environment; no secret in the code |
| Pairing registry | maps a WhatsApp number to one agent's portal room; short one-time link codes |
| Bridge bot | a Windy Chat account that posts each incoming message into the portal room and relays the agent's reply out |
| Outbound sender | sends the reply through the provider's WhatsApp API |
| Web app | the webhook, plus /version (config status) and /health |
| Provisioning script | makes a portal room and a link code for one agent |

## Pairing flow

1. The owner makes a link code for their agent (a provisioning step on the Windy side).
2. The person texts `link <code>` to the shared Windy number.
3. The gateway checks the code, stores WhatsApp number → that agent's portal room, and confirms.
4. From then on, each message from that number lands in that room; the agent answers there; the gateway sends the
   answer back to the same WhatsApp number.

## What is NOT decided yet (before any build)

- **Who may pair.** Today's pairing trusts whoever holds the code. A real version should tie a pairing to the owner's
  Windy account and let the owner see and remove pairings.
- **Who the agent is talking to.** A WhatsApp sender is a stranger to the agent unless paired by its owner, so it
  should get the agent's non-owner band (no owner tools, no owner memory), the same as any other stranger.
- **Records and consent.** Message retention, the provider's terms, opt-out words, and the privacy policy's wording
  (Windy Legal).
- **Money.** Provider messaging costs per conversation; who pays, and any limits (Windy CFO).
- **Scale.** Per-agent numbers, message templates for agent-started messages, and showing the agent's own name as
  the sender.

## Going live later (outline only)

A real WhatsApp sender needs the provider's and Meta's business approval. After that, the outbound sender's "from"
changes to the approved number and its inbound webhook points at the gateway; nothing else in the design changes.
Each step (host, number, provider account, flags) would need its own GO, BEFORE/AFTER, and a way back.
