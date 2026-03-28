# BRAND-ARCHITECTURE.md — The Windy Family

_Last updated: 28 March 2026_
_Status: ACTIVE — This is the canonical source of truth for all branding decisions._

---

## The Vision

Seven interlocking companies that form a flywheel — each one feeds the others, but each can stand alone. Every product makes every other product more valuable.

**Tagline:** _"Stop typing through a straw. Speak your vision into existence."_

---

## The Family

### 🎙️ Windy Word
**What it does:** Voice recording → text transcription (Speech-to-Text)
**Role in the family:** The gateway. Customer acquisition engine. Top of the funnel.
**Website:** windyword.com
**Revenue model:** Subscriptions + lifetime purchases
**Pricing tiers:**
- **Free:** $0 — 1 language, 3 engines, 2-min recordings
- **Windy Pro:** $99 lifetime / $49/yr / $4.99/mo — All 15 engines, 99 langs, 15-min
- **Windy Ultra:** $199 lifetime / $79/yr / $8.99/mo — + 60-min, translation, 25 pairs _(RECOMMENDED)_
- **Windy Max:** $299 lifetime / $149/yr / $14.99/mo — + unlimited, TTS, glossaries, 100 pairs
**Platforms:** Desktop (Electron), iOS, Android
**Ship priority:** #1 — Ships first, generates revenue, proves the market

### 🌍 Windy Traveler
**What it does:** Translation engine marketplace — language pair specialist models
**Role in the family:** The cash cow. Pure margin once models are built.
**Website:** windytraveler.com
**Revenue model:** Individual pairs ($6.99 each) + bundles
**Bundles:**
- **Traveler:** $49 — 25 pairs
- **Polyglot:** $149 — 200 pairs
- **Marco Polo:** $399 — ALL 3,500+ pairs
**The moat:** 2,500 fine-tuned translation pair models. Each is a legally distinct derivative work via LoRA.
**Ship priority:** #2 — Pairs already being built (1,188 on HuggingFace, targeting 2,500). Monetized through Windy Word from day one.

### 💬 Windy Chat
**What it does:** Encrypted messaging with built-in real-time translation
**Role in the family:** The distribution engine. Every cross-language conversation drives Traveler pair purchases.
**Website:** windychat.com
**Revenue model:** Freemium + premium features
**Architecture:** Matrix protocol — E2E encrypted, decentralized
**Strategic vision:** WhatsApp killer. First bot-to-bot communication platform. Agent-friendly.
**Ship priority:** #4 — Needs critical mass of users and a working Traveler engine first

### 🪰 HiFly (Open Source Framework)
**What it does:** Open-source AI agent framework — the engine that powers personal AI companions
**Role in the family:** The open-source foundation. Like Android to Google Play Services. Attracts developers, creates ecosystem gravity, establishes the standard for personal AI agents.
**Website:** hifly.ai
**Revenue model:** None — fully open source (MIT). Revenue comes from the ecosystem products built on top.
**Signature:** The "IT'S ALIVE! IT'S ALIVE! THE FLY IS ALIVE!" hatching ceremony — hardcoded into every HiFly descendant, forever. Like the Linux penguin or the Mac startup chime. Plays every time an agent hatches, anywhere in the world, for all eternity.
**What it includes:**
- Multi-provider LLM brain (OpenAI, Anthropic, Grok, Gemini, DeepSeek, Mistral, Ollama)
- SQLite memory with vector search and knowledge graph
- Personality engine with 8 presets and 10 slider dimensions
- Skills system with self-improvement and evaluation gates
- Trust Dashboard (browser-based control panel)
- CLI tools (`hifly go`, `hifly doctor`, `hifly update`, etc.)
- SMS channel (Twilio), Email channel (SendGrid)
- Cross-platform: Mac, Linux, Windows
- `curl -fsSL https://get.hifly.ai | bash` — one-liner install
**What it does NOT include:** Windy Chat, Windy Pro API integration, Matrix auto-provisioning, ecosystem status panel. Those are Windy Fly exclusives.
**Ship priority:** #6 — Ships after Windy Fly proves the concept. Open-sourced to attract developers and create ecosystem gravity.

### 🪰 Windy Fly (Ecosystem-Integrated Agent)
**What it does:** HiFly + deep integration with the entire Windy ecosystem. Your personal AI companion that is born connected.
**Role in the family:** The nervous system. Connects every other product. The reason users never leave the ecosystem. The ultimate customer retention engine.
**Website:** windyfly.ai
**Revenue model:** Freemium — free tier with daily budget caps, premium tier with higher limits, enterprise tier for businesses deploying agents for their customers.
**Strategic vision:** Every Windy Fly hatched = one new user on Windy Chat. Every user on Windy Chat = potential Traveler pair purchases, Word subscriptions, Clone training data. Windy Fly is the gravity well that pulls everything together.
**The "Born Into" experience:** When a Windy Fly hatches, it is immediately:
- Connected to **Windy Chat** (Matrix) — no BotFather, no tokens, no setup. Born with a chat identity.
- Ready to **send SMS** and **email** — communication channels live from birth.
- Connected to **199 languages** via Windy Traveler — translate anything, instantly.
- Able to **search voice recordings** from Windy Word — "what did I say in that meeting?"
- Aware of **clone training status** from Windy Clone — "your voice clone is 73% ready."
- Backed by **Windy Cloud** — memory, config, and personality synced across devices.
**The moat:** Windy Chat is exclusive to Windy Fly. It is NOT in HiFly core. This means every fork of HiFly that wants native chat has to either build their own infrastructure or use Windy Fly. This is the Google Play Services strategy — Android is open, but the ecosystem is not.
**User acquisition flow:**
1. User runs `windy go` (one command)
2. Pastes an API key (or signs up for free Gemini — guided walkthrough)
3. Agent hatches with "IT'S ALIVE!" ceremony
4. User receives SMS with link to download Windy Chat app
5. Opens app → agent is already there, already chatting
6. User never opens a terminal again. Lives in the chat app.
7. Agent drives Traveler pair purchases, Word subscriptions, Clone data accumulation.
**Ship priority:** #3 — Ships alongside Windy Chat. They are symbiotic — one without the other is incomplete.

### 🧬 Windy Clone
**What it does:** Converts accumulated voice & text data into a digital likeness — voice clone, avatar, soul file
**Role in the family:** The moonshot. Smallest market today, enormous market in 3-5 years.
**Website:** windyclone.com
**Revenue model:** TBD — likely subscription for ongoing clone refinement
**Strategic vision:** Digital identity persistence. The consumer entry point to digital immortality.
**Ship priority:** #3 — Builds on data from Windy Word users over time

### ☁️ Windy Cloud
**What it does:** Storage, sync, and model delivery infrastructure across all four products
**Role in the family:** The backbone. Every product depends on it.
**Website:** windycloud.com
**Revenue model:** Included in subscriptions + enterprise tiers. Potential future platform play for third-party developers.
**Ship priority:** #5 — Exists as internal infrastructure from day one, becomes an external product later

---

## Parent Company

**TBD** — Under consideration. Candidates include:
- Windy Labs
- Windy Pro Labs (current working name)
- Windstorm Inc
- Other

The parent company is the holding entity that owns stakes in all seven product companies. Enables:
- Selling individual companies without losing the others
- Taking investment in one product without diluting the rest
- Tax and liability isolation
- Independent valuations per product

---

## The Flywheel

```
Windy Word (captures voice → text data)
    ↓
Windy Traveler (translates that text → sells pair models)
    ↓
Windy Chat (uses translations in real-time → distribution engine)
    ↓
Windy Fly (AI agent born INTO Chat → orchestrates everything)
    ↓  ↑ drives pair purchases, Word subs, Clone data
Windy Clone (uses ALL accumulated voice/text → digital likeness)
    ↓
Windy Cloud (stores and syncs everything → infrastructure backbone)
    ↑
    └── feeds back to Word (more devices, more capture)

               ┌─────────────────────────┐
               │  HiFly (open source)    │
               │  The engine underneath  │
               │  Windy Fly. Attracts    │
               │  developers. Creates    │
               │  ecosystem gravity.     │
               └────────┬────────────────┘
                        │ forks into
                        ▼
               ┌─────────────────────────┐
               │  Windy Fly (ecosystem)  │
               │  Born into Windy Chat.  │
               │  Every hatch = new user │
               │  on YOUR platform.      │
               └─────────────────────────┘
```

### The Android / Google Play Services Strategy

Google did exactly what we're doing. They made two things:

- **Android (AOSP)** — open source, anyone can fork it. This is **HiFly**.
- **Google Play Services** — NOT open source. Gmail, Maps, Play Store, push notifications. This is **Windy Chat**.

Samsung can fork Android. Amazon did fork Android (Fire tablets). But they can't fork Google Maps or Gmail. That's Google's moat. That's why 95% of Android phones still run Google's version — because the ecosystem is too valuable to give up.

**Windy Chat is our Google Play Services.** It's the reason people choose Windy Fly over a generic HiFly fork.

**The mapping:**

- **HiFly** = Android (AOSP). Open source. Anyone can fork it. Developers love it. Creates the standard.
- **Windy Fly** = Google's Android. HiFly + Windy Chat + Windy Pro API + ecosystem integration.
- **Windy Chat** = Google Play Services. NOT open source. The moat. The reason 95% of users choose Windy Fly over a generic HiFly fork.

Someone can fork HiFly and build their own agent. But they can't fork Windy Chat. They can't fork the network of users. They can't fork the "Born Into" experience. If they want that, they use Windy Fly. And every Windy Fly hatched grows YOUR network.

### Why This Is the Right Call for Money and Growth

| Strategy | Network effect | Revenue | Competitive moat |
|----------|---------------|---------|-----------------|
| Chat in HiFly (everyone gets it) | Huge but you pay for everyone's infrastructure | None — it's free | Zero — competitors fork it |
| Chat in Windy Fly only | Grows with YOUR users | Freemium upsell, premium features | Massive — nobody else has it |

If you put chat in HiFly core, someone forks HiFly tomorrow, slaps their logo on it, and uses YOUR Synapse infrastructure for free. You're paying for their users' chat. That's a terrible deal.

If chat is Windy Fly only, every agent hatched from Windy Fly **grows your network**. Grandma at the hotel ballroom hatches her Windy Fly, she's instantly in the Windy Chat network. She can talk to her agent, her agent can talk to other agents, she can message other Windy users. That's a WhatsApp-style flywheel that only YOU control.

### The "Born Into" Experience (Grandma at the Hotel Ballroom)

Here's what happens when a non-technical user hatches their Windy Fly:

```
windy go

  🪰 IT'S ALIVE!!! IT'S ALIVE!!! THE FLY IS ALIVE!!!

  ╭──── Born Into the Windy Ecosystem ────╮
  │                                        │
  │  ✓ 💬  Windy Chat — connected          │
  │  ✓ 🧠  AI Brain — gemini-2.5-flash     │
  │  ✓ 🎛️  Dashboard — localhost:3000      │
  │                                        │
  ╰────────────────────────────────────────╯

  📱 We just sent you a text message!
     Download Windy Chat to talk to your
     agent from your phone.
```

She gets an SMS with a link. Downloads the app. Opens it. Her Windy Fly is already there, already chatting. She never opens a terminal again. She lives in the chat app from now on.

**That's the growth engine.** Every Windy Fly hatched = one new user on the chat platform. Every user on the chat platform = someone who might invite friends. WhatsApp grew the exact same way — utility first (free messaging), network effect second.

### What HiFly Gets (and What It Does NOT Get)

**HiFly (the open-source framework) includes:**

- The "IT'S ALIVE!" hatching ceremony (hardcoded, forever)
- CLI chat (`hifly start --cli`)
- The ability to plug in ANY chat platform (Telegram, Discord, Slack, whatever)
- SMS and email channels (Twilio, SendGrid)
- The full agent brain, memory, skills, dashboard
- Multi-provider LLM support (11 providers)
- Cross-platform: Mac, Linux, Windows

**HiFly does NOT include:**

- Windy Chat baked in
- Auto-provisioned Matrix bot on chat.windypro.com
- The "Born Into the Windy Ecosystem" panel
- The SMS-on-hatch with app download link
- Contact discovery across the Windy network
- Push notifications through Windy infrastructure
- Windy Pro API tools (translation, recordings, clone status)

Someone who forks HiFly and wants chat has to set up their own Matrix server, their own push notifications, their own onboarding. That's weeks of work. Or they can just use Windy Fly and get it all for free on hatch.

### The White-Label Question

**Don't white-label the chat itself. White-label the agent.**

Let businesses customize their Windy Fly's name, personality, skills, and branding. But the chat network stays Windy Chat. That's how you maintain network effect.

Every business that deploys a Windy Fly agent for their customers is putting those customers INTO the Windy Chat network. The business gets a custom agent. You get the network growth. Everyone wins.

### Infrastructure Already Built

The following pieces are production-ready:

1. **Matrix auto-provisioning** — `matrix_provision.py` in windy-agent repo
2. **Synapse homeserver** — running at `chat.windypro.com` (K1)
3. **Chat onboarding service** — running at port 8101 (K2)
4. **Push notification gateway** — FCM + APNs at port 8103 (K6)
5. **Contact discovery** — Signal-style hash matching at port 8102 (K3)
6. **Encrypted cloud backup** — Cloudflare R2 at port 8104 (K8)
7. **Mobile app** — React Native + Expo with chat tab (`windy-pro-mobile`)

What remains to wire up for the "SMS on hatch" experience:

- During `windy go`, ask for phone number (optional)
- Call the chat-onboarding service to provision the user
- Send SMS via Twilio with Windy Chat app download link
- Bot auto-joins user's DM room on chat.windypro.com

**Bottom line:** Windy Chat is the moat. Keep it in Windy Fly. Let HiFly be the open engine that makes people WANT to use the ecosystem.

---

## Naming Philosophy

### Why "Windy Word"?

The concept of **creative power through spoken word** is the single most universal theological idea on Earth:

| Tradition | Concept | Believers |
|-----------|---------|-----------|
| Judaism | Ten Utterances — "And God said, let there be light" | ~15M |
| Christianity | Logos — "In the beginning was the Word" (John 1:1) | ~2.4B |
| Islam | Kun fayakun — "Be, and it is" (appears 8× in the Quran) | ~1.9B |
| Hinduism | Om / Vak / Shabda — primordial creative sound | ~1.2B |
| Sikhism | Shabad — the divine Word that created the universe | ~30M |
| Zoroastrianism | Manthra — sacred utterance with creative power | ~200K |
| **Total** | | **~5.5 billion people** |

"Windy Word" taps into a concept that 5.5 billion people already believe: **the spoken word has the power to create reality.** This isn't clever marketing — it's a universal human truth built into the product name.

### Naming Rules

- Every product name is **descriptive** — tells you what it does without explanation
- Every product name passes the **cocktail party test** — list them and people _get it_
- **"Pro"** is reserved as a **tier modifier**, not a product name (Windy Word Pro, Windy Traveler Pro, etc.)
- All names are **short, memorable, and don't collide** with major existing brands

---

## Model Protection Architecture

### The Threat
Buy Marco Polo ($399) → download all 3,500+ .bin model files → airplane mode → request refund → keep models forever.

### Defense Stack (4 layers)

1. **Encrypted Model Files** — Models stored encrypted with AES-256. Key derived from `HKDF(licenseToken + deviceId + appSecret)`. No valid license on this device = useless blobs. Decryption in memory only, never written unencrypted to disk.

2. **License Heartbeat** — App checks entitlement every 48 hours. Tiered offline grace periods:
   - Free: 24 hours
   - Pro: 7 days
   - Ultra: 14 days
   - Max / Marco Polo: 30 days
   - After grace period: models locked (not deleted) until re-verified

3. **RevenueCat Refund Webhooks** — When Apple/Google processes a refund, RevenueCat fires an event → flag user → next online check = models locked and deleted.

4. **Model Watermarking** — Each downloaded model gets a micro LoRA modification unique to the buyer's license ID. Invisible to performance, forensically traceable if models appear on torrent sites.

### What We Accept
- Jailbreak/root extraction of raw weights cannot be prevented (same problem Netflix/Spotify face)
- People who would do this were never going to pay anyway
- The 30-day money-back guarantee is safe — Apple/Google have anti-abuse systems, and our heartbeat catches the rest

---

## Current Repository Structure

| Repo | Contains | Status |
|------|----------|--------|
| `windy-pro` (GitHub: sneakyfree/windy-pro) | Desktop Electron app, Python backend, installer wizard, account server, Synapse/Matrix infra, chat services | Active |
| `windy-pro-mobile` (GitHub: sneakyfree/windy-pro-mobile) | React Native + Expo mobile app (iOS + Android) | Active |
| `windy-agent` (GitHub: sneakyfree/windy-agent) | Windy Fly — AI agent brain, gateway, trust dashboard. Will fork into HiFly (generic) + Windy Fly (ecosystem) | Active |

All repos will be rebranded to reflect the final product names when the time is right. This file lives in all repos as the single source of branding truth.

---

## Key Dates

- **2025:** Windy Pro development begins (desktop + mobile)
- **2026-01:** HuggingFace model pipeline starts (target: 3,500+ pairs)
- **2026-03-19:** Brand architecture formalized (this document)
- **2026-03-27:** Windy Fly agent development begins (windy-agent repo)
- **2026-03-28:** HiFly/Windy Fly fork strategy defined. "IT'S ALIVE!" ceremony hardcoded as core HiFly DNA. Windy Chat designated as ecosystem-exclusive moat (not in HiFly core).
- **TBD:** Domain purchases, website launches, app store listings updated
- **TBD:** HiFly open-source release (after Windy Fly proves the concept)

---

_This document is the canonical reference for all branding, naming, and product family decisions. All AG tabs, Kit clones, and developers should read this before doing any branding-related work._

_This document lives in: `windy-pro/`, `windy-pro-mobile/`, and `windy-agent/` repos._
