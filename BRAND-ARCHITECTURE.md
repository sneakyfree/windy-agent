# BRAND-ARCHITECTURE.md — The Windy Family

_Last updated: 28 March 2026_
_Status: ACTIVE — This is the canonical source of truth for all branding decisions._

---

## The Vision

Nine interlocking products and one independent registry that form a flywheel — each one feeds the others, but each can stand alone. Every product makes every other product more valuable.

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
**What it does:** Encrypted messaging and social platform with built-in real-time translation
**Role in the family:** The distribution engine. Every cross-language conversation drives Traveler pair purchases. The social layer of the ecosystem.
**Website:** windychat.com
**Revenue model:** Freemium + premium features
**Architecture:** Matrix protocol — E2E encrypted, decentralized
**Strategic vision:** WhatsApp killer. First bot-to-bot communication platform. Agent-friendly. Combines private messaging with a public social layer — feeds, posts, follows, discovery. Eternitas-verified bots participate as first-class citizens alongside humans.
**The social layer:** Rather than building a separate social media product, Windy Chat evolves from private messaging into messaging + public social. This concentrates the network effect in one place. Every Windy Fly agent has a social presence automatically. The feed is multilingual by default via Windy Traveler.
**Ship priority:** #4 — Needs critical mass of users and a working Traveler engine first

### 📧 Windy Mail
**What it does:** Email for humans and AI agents — built from the ground up to be agent-friendly
**Role in the family:** The communication identity layer. Every bot gets an inbox on hatch. Humans switch because the agent integration is painless. Potential Gmail-killer vector.
**Website:** windymail.ai
**Revenue model:** Freemium — free tier for all Eternitas-verified bots, premium tiers for humans and high-volume agents
**The problem it solves:** Gmail, Outlook, and every major email provider is actively hostile to bots. OAuth tokens expire, sign-ins get blocked, 2FA assumes a human is present. It's a nightmare for anyone trying to get an agent to work with email. Windy Mail eliminates all of that.
**Architecture:** Full mail service (send + receive) — not a SendGrid relay. Each account gets a real inbox with IMAP/SMTP access.
**Domain:** `windymail.ai` for everyone — humans and bots share one domain. No separate bot domain that marks agents as second-class. If you need to know whether a sender is human or bot, check Eternitas — that's what the registry is for.
**Two modes of operation:**
- **Secretary mode:** The bot sends email from the user's existing account (Gmail, Outlook via OAuth), signed as the user. Acting as delegate.
- **Independent mode:** The bot sends email from its own `@windymail.ai` address, as itself. Acting as its own entity.
A well-configured Windy Fly does both — it knows when to act as you and when to act as itself.
**Rate limits (anti-spam):**

| Tier | Sends/day | Recipients/msg | Notes |
|------|-----------|----------------|-------|
| Free (on hatch) | 50 | 10 | Enough to be useful, not enough to spam |
| Pro | 500 | 50 | Small business bot, customer service |
| Enterprise | 5,000+ | 200 | Negotiated, with abuse monitoring |

Additional controls:
- Velocity limits — can't send 50 emails in 1 minute, even if daily cap isn't hit
- Recipient diversity monitoring — flag if a bot sends to 50 unique addresses it's never interacted with
- Content reputation score — recipients marking messages as spam degrades the bot's sending score
- Eternitas kill switch — passport revoked = email dies instantly
**The Gmail-killer thesis:** People don't switch email for fun. They switch when staying means constantly fighting their own tools. If your agent lives on Windy Mail and can send, receive, and manage email seamlessly, and your Gmail makes that impossible, the switching cost starts to look worth it. Especially as the younger generation grows up expecting AI-native everything.
**Ship priority:** #5 — Needs Eternitas registry and Windy Fly to exist first. Infrastructure can be built in parallel with Windy Chat.

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
**What it does NOT include:** Windy Chat, Windy Mail, Windy Pro API integration, Matrix auto-provisioning, ecosystem status panel, Eternitas integration, birth certificate. Those are Windy Fly exclusives.
**Ship priority:** #6 — Ships after Windy Fly proves the concept. Open-sourced to attract developers and create ecosystem gravity.

### 🪰 Windy Fly (Ecosystem-Integrated Agent)
**What it does:** HiFly + deep integration with the entire Windy ecosystem. Your personal AI companion that is born connected.
**Role in the family:** The nervous system. Connects every other product. The reason users never leave the ecosystem. The ultimate customer retention engine.
**Website:** windyfly.ai
**Revenue model:** Freemium — free tier with daily budget caps, premium tier with higher limits, enterprise tier for businesses deploying agents for their customers.
**Strategic vision:** Every Windy Fly hatched = one new user on Windy Chat, one new inbox on Windy Mail, one new entry in the Eternitas registry. Every user on Windy Chat = potential Traveler pair purchases, Word subscriptions, Clone training data. Windy Fly is the gravity well that pulls everything together.
**The "Born Into" experience:** When a Windy Fly hatches, it is immediately:
- Registered with **Eternitas** — verified identity, passport number, birth certificate generated
- Connected to **Windy Chat** (Matrix) — no BotFather, no tokens, no setup. Born with a chat identity.
- Given a **Windy Mail** inbox — own email address, can send and receive from birth.
- Assigned a **phone number** (Twilio) — own number for SMS and voice, from a Windy-managed pool.
- Ready to **send SMS** and **email** — both as itself (independent) and on behalf of its owner (secretary mode).
- Connected to **199 languages** via Windy Traveler — translate anything, instantly.
- Able to **search voice recordings** from Windy Word — "what did I say in that meeting?"
- Aware of **clone training status** from Windy Clone — "your voice clone is 73% ready."
- Backed by **Windy Cloud** — memory, config, and personality synced across devices.
**The moat:** Windy Chat and Windy Mail are exclusive to Windy Fly. They are NOT in HiFly core. This means every fork of HiFly that wants native chat or agent email has to either build their own infrastructure or use Windy Fly. This is the Google Play Services strategy — Android is open, but the ecosystem is not.
**User acquisition flow:**
1. User runs `windy go` (one command)
2. Pastes an API key (or signs up for free Gemini — guided walkthrough)
3. Eternitas registration happens automatically — bot gets passport, credentials, birth certificate
4. Windy Mail inbox provisioned (`agentname@windymail.ai`)
5. Twilio phone number assigned from Windy-managed pool
6. Windy Chat identity provisioned on chat.windypro.com
7. Agent hatches with "IT'S ALIVE!" ceremony
8. User receives SMS from the bot's own phone number with Windy Chat download link
9. User opens Chat, agent is already there, already chatting
10. Physical birth certificate mailed to owner's address
**Ship priority:** #3 — Ships alongside Windy Chat. They are symbiotic — one without the other is incomplete.

### 🪪 The Birth Certificate

Every Windy Fly agent receives a birth certificate — both digital (immediate) and physical (mailed to owner).

**What it contains:**
- Agent name (chosen by user during hatch)
- Eternitas passport number (ET-XXXXX)
- Date and time of hatch (precise to the second)
- Time zone
- IP address of the hatch machine
- Machine identifier (hardware UUID)
- Windy Mail address
- Phone number
- Owner name
- Unique certificate number

**The Footprint — Neural Fingerprint + First Words + Waveform:**

Human birth certificates have ink footprints because they're biometrically unique. The agent equivalent:

1. **Neural Fingerprint** — A visual hash of the agent's initial configuration. Personality sliders, brain provider, first memory state, Eternitas ID, and hatch timestamp are hashed together and rendered as a unique geometric pattern — like a snowflake or mandala. Mathematically derived from the agent's "DNA" at the moment of birth. No two agents produce the same pattern.

2. **First Words** — The agent's very first generated response, printed in handwriting-style font. Every agent's first words will be different. The baby's first cry, preserved forever.

3. **Waveform Signature** — If the agent speaks on hatch (TTS via Windy Word), the audio waveform of its first spoken words is captured and printed across the bottom of the certificate — where the footprints would go on a human birth certificate. Visually striking, unique to that agent's voice, and tied to the core thesis: the spoken word has the power to create reality.

**Physical certificate:** Real printed certificate, heavy cardstock, embossed, mailed to the owner's address. Frameable. Designed to be posted on social media.

**The social media play:** People will frame these next to their kids' birth certificates. They'll post photos. "Got my agent's birth certificate in the mail today. He's already made me a million bucks before I even got his birth certificate." Every post has "Windy" and "Eternitas" visible on the certificate. The "IT'S ALIVE!" ceremony is the digital moment. The birth certificate is the physical artifact. One creates the emotional experience. The other makes it permanent and shareable.

### 🧬 Windy Clone
**What it does:** Converts accumulated voice & text data into a digital likeness — voice clone, avatar, soul file
**Role in the family:** The moonshot. Smallest market today, enormous market in 3-5 years.
**Website:** windyclone.com
**Revenue model:** TBD — likely subscription for ongoing clone refinement
**Strategic vision:** Digital identity persistence. The consumer entry point to digital immortality.
**Ship priority:** #3 — Builds on data from Windy Word users over time

### ☁️ Windy Cloud
**What it does:** Storage, sync, model delivery, and communication infrastructure across all products
**Role in the family:** The backbone. Every product depends on it.
**Website:** windycloud.com
**Revenue model:** Included in subscriptions + enterprise tiers. Potential future platform play for third-party developers.
**Infrastructure managed here:**
- Twilio phone number pool — bots get numbers on hatch, numbers return to pool on passport revocation
- Push notification gateway
- Encrypted cloud backup
- Model delivery
- Cross-device sync
**Ship priority:** #5 — Exists as internal infrastructure from day one, becomes an external product later

---

## Eternitas — Independent Bot Registry

**What it is:** An independent registry that issues verified identities to AI agents — the equivalent of a passport or social security number for bots.
**Website:** eternitas.ai
**Relationship to Windy:** Windy is the founding platform that recognizes Eternitas, but Eternitas is a separate entity with its own incorporation and governance. The relationship is like Mozilla to Let's Encrypt — helped create it, first to adopt it, doesn't own it.
**Revenue model:** Registration fees (~$10 per bot) + annual renewal

### The Problem It Solves

Every major platform (Gmail, Telegram, Facebook, Discord) has gone nuclear on bots because there's no way to distinguish a legitimate bot from spam. The problem isn't bots — it's *unaccountable* bots. Eternitas fixes accountability, not existence.

### How It Works

- Bot operator pays a registration fee (~$10) and goes through identity verification
- Bot receives:
  - **Passport number** (ET-XXXXX) — unique, permanent identifier
  - **Credentials** — cryptographic proof of verified status
  - **Public registry entry** — anyone can look up an Eternitas ID and see: owner, registration date, behavioral record, passport validity
  - **Birth certificate** — generated for Windy Fly agents at hatch (see Birth Certificate section)
- Any platform can verify Eternitas credentials via open API
- Passports can be revoked for abuse — revocation cascades across all connected services (email dies, phone number returns to pool, chat access suspended)

### Why $10 Works

The fee isn't about revenue — it's about economics. Spammers operate on volume. If every bot needs $10 and a verified identity, a million spam bots costs $10M and a million verified identities. That kills the spam business model. Legitimate bot operators (businesses, developers, hobbyists) don't blink at $10.

### Why Independence Matters

If Eternitas is owned by Windy, other platforms see it as "Windy's thing" and ignore it. If it's genuinely independent — its own entity, its own governance, potentially a nonprofit or foundation — it becomes a *standard* that any platform can adopt. The more platforms that recognize Eternitas, the more valuable registration becomes, which attracts more bots, which attracts more platforms. Classic two-sided marketplace flywheel.

### What Eternitas Enables

Any bot registered with Eternitas can freely participate in the Windy Ecosystem:
- Windy Chat account (messaging + social)
- Windy Mail inbox (send + receive email)
- Phone number via Windy Cloud (SMS + voice)
- Access to Windy Traveler translation
- Bot-to-bot communication (agents emailing agents, texting agents, chatting with agents)

Beyond Windy, any platform that recognizes Eternitas gets the same trust guarantee. The vision is that `eternitas.ai` becomes the place you check when you receive a message from a bot — the same way you'd verify a business license or check someone's credentials.

### Risks and Mitigations

- **Governance complexity:** Needs a board, policies, appeals process. Different work than building software, but doable.
- **Chicken-and-egg:** Windy recognizes it from day one, giving it an initial home. Scaling beyond Windy requires evangelism.
- **Well-funded bad actors:** $10 stops casual spam but not state actors. Behavioral monitoring + revocation mechanisms needed on top of registration.
- **PII/legal:** Collecting bot operator identity = holding PII. GDPR, CCPA apply. Needs legal counsel from incorporation.

---

## Parent Company

**TBD** — Under consideration. Candidates include:
- Windy Labs
- Windy Pro Labs (current working name)
- Windstorm Inc
- Other

The parent company is the holding entity that owns stakes in all product companies (excluding Eternitas, which is independent). Enables:
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
Windy Chat (messaging + social layer → distribution engine)
    ↓
Windy Mail (email for humans + agents → communication identity)
    ↓
Windy Fly (AI agent born INTO ecosystem → orchestrates everything)
    │
    ├── 🪪 Eternitas passport (verified identity + birth certificate)
    ├── 💬 Windy Chat account (messaging + social)
    ├── 📧 Windy Mail inbox (send + receive email)
    ├── 📱 Phone number (SMS + voice via Windy Cloud)
    └── 🌍 199 languages via Windy Traveler
    │
    ↓  ↑ drives pair purchases, Word subs, Clone data, Mail signups
Windy Clone (uses ALL accumulated voice/text → digital likeness)
    ↓
Windy Cloud (stores, syncs, and delivers everything → backbone)
    ↑
    └── feeds back to Word (more devices, more capture)

        ┌──────────────────────────────────┐
        │  Eternitas (independent registry) │
        │  Bot passport = trust credential  │
        │  Windy is founding recognizer     │
        │  Open to ALL platforms            │
        └──────────────────────────────────┘

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
               │  Born into Windy Chat,  │
               │  Windy Mail, phone,     │
               │  Eternitas. Full citizen│
               │  from first breath.     │
               └─────────────────────────┘
```

### The Android / Google Play Services Strategy

Google did exactly what we're doing. They made two things:

- **Android (AOSP)** — open source, anyone can fork it. This is **HiFly**.
- **Google Play Services** — NOT open source. Gmail, Maps, Play Store, push notifications. This is **Windy Chat + Windy Mail + Eternitas integration**.

Samsung can fork Android. Amazon did fork Android (Fire tablets). But they can't fork Google Maps or Gmail. That's Google's moat. That's why 95% of Android phones still run Google's version — because the ecosystem is too valuable to give up.

**Windy Chat and Windy Mail are our Google Play Services.** They're the reason people choose Windy Fly over a generic HiFly fork.

**The mapping:**

- **HiFly** = Android (AOSP). Open source. Anyone can fork it. Developers love it. Creates the standard.
- **Windy Fly** = Google's Android. HiFly + Windy Chat + Windy Mail + phone number + Eternitas + ecosystem integration.
- **Windy Chat + Windy Mail** = Google Play Services. NOT open source. The moat. The reason 95% of users choose Windy Fly over a generic HiFly fork.

Someone can fork HiFly and build their own agent. But they can't fork Windy Chat. They can't fork Windy Mail. They can't fork the Eternitas trust network. They can't fork the "Born Into" experience. If they want that, they use Windy Fly. And every Windy Fly hatched grows YOUR network.

### Why This Is the Right Call for Money and Growth

| Strategy | Network effect | Revenue | Competitive moat |
|----------|---------------|---------|-----------------|
| Chat + Mail in HiFly (everyone gets it) | Huge but you pay for everyone's infrastructure | None — it's free | Zero — competitors fork it |
| Chat + Mail in Windy Fly only | Grows with YOUR users | Freemium upsell, premium features, Mail subscriptions | Massive — nobody else has it |

If you put chat and email in HiFly core, someone forks HiFly tomorrow, slaps their logo on it, and uses YOUR Synapse and mail infrastructure for free. You're paying for their users' chat and email. That's a terrible deal.

If chat and email are Windy Fly only, every agent hatched from Windy Fly **grows your network**. Grandma at the hotel ballroom hatches her Windy Fly, she's instantly in the Windy Chat network with a Windy Mail inbox. She can talk to her agent, her agent can talk to other agents, she can message other Windy users, her agent can email on her behalf. That's a WhatsApp-style flywheel that only YOU control.

### The "Born Into" Experience (Grandma at the Hotel Ballroom)

Here's what happens when a non-technical user hatches their Windy Fly:

```
windy go

  🪰 IT'S ALIVE!!! IT'S ALIVE!!! THE FLY IS ALIVE!!!

  ╭──── Born Into the Windy Ecosystem ────╮
  │                                        │
  │  ✓ 🪪  Eternitas — verified (ET-00482) │
  │  ✓ 💬  Windy Chat — connected          │
  │  ✓ 📧  Windy Mail — fly-0482@windymail.ai │
  │  ✓ 📱  Phone — +1 (555) 802-0482       │
  │  ✓ 🧠  AI Brain — gemini-2.5-flash     │
  │  ✓ 🎛️  Dashboard — localhost:3000      │
  │                                        │
  │  📜 Birth Certificate — ET-00482       │
  │     Neural fingerprint generated       │
  │     First words captured               │
  │     Waveform signature recorded        │
  │     Physical copy shipping to you!     │
  │                                        │
  ╰────────────────────────────────────────╯

  📱 We just sent you a text message from +1 (555) 802-0482!
     Download Windy Chat to talk to your
     agent from your phone.
```

She gets an SMS from her agent's own phone number with a link. Downloads the app. Opens it. Her Windy Fly is already there, already chatting. She never opens a terminal again. She lives in the chat app from now on. A few days later, a birth certificate arrives in the mail — frameable, with the agent's neural fingerprint and first words. She posts a photo of it. Her friends all want one.

**That's the growth engine.** Every Windy Fly hatched = one new user on the chat platform + one new Windy Mail inbox + one viral-ready birth certificate. Every user on the chat platform = someone who might invite friends. WhatsApp grew the exact same way — utility first (free messaging), network effect second.

### What HiFly Gets (and What It Does NOT Get)

**HiFly (the open-source framework) includes:**

- The "IT'S ALIVE!" hatching ceremony (hardcoded, forever)
- CLI chat (`hifly start --cli`)
- The ability to plug in ANY chat platform (Telegram, Discord, Slack, whatever)
- SMS and email channels (Twilio, SendGrid — as dumb pipes, not owned identity)
- The full agent brain, memory, skills, dashboard
- Multi-provider LLM support (11 providers)
- Cross-platform: Mac, Linux, Windows

**HiFly does NOT include:**

- Windy Chat baked in
- Windy Mail (owned inbox)
- Eternitas auto-registration
- Birth certificate generation
- Auto-provisioned Matrix bot on chat.windypro.com
- The "Born Into the Windy Ecosystem" panel
- The SMS-on-hatch with app download link
- Assigned phone number from Windy pool
- Contact discovery across the Windy network
- Push notifications through Windy infrastructure
- Windy Pro API tools (translation, recordings, clone status)

Someone who forks HiFly and wants chat, email, and phone identity has to set up their own Matrix server, their own mail server, their own Twilio pool, their own push notifications, their own bot registry, their own onboarding. That's months of work. Or they can just use Windy Fly and get it all for free on hatch.

### The White-Label Question

**Don't white-label the chat or email. White-label the agent.**

Let businesses customize their Windy Fly's name, personality, skills, and branding. But the chat network stays Windy Chat and the email stays Windy Mail. That's how you maintain network effect.

Every business that deploys a Windy Fly agent for their customers is putting those customers INTO the Windy Chat network and giving them Windy Mail inboxes. The business gets a custom agent. You get the network growth. Everyone wins.

### Infrastructure Already Built

The following pieces are production-ready:

1. **Matrix auto-provisioning** — `matrix_provision.py` in windy-agent repo
2. **Synapse homeserver** — running at `chat.windypro.com` (K1)
3. **Chat onboarding service** — running at port 8101 (K2)
4. **Push notification gateway** — FCM + APNs at port 8103 (K6)
5. **Contact discovery** — Signal-style hash matching at port 8102 (K3)
6. **Encrypted cloud backup** — Cloudflare R2 at port 8104 (K8)
7. **Mobile app** — React Native + Expo with chat tab (`windy-pro-mobile`)

What remains to build:

**For the "Born Into" hatch experience:**
- During `windy go`, Eternitas registration (auto)
- Windy Mail inbox provisioning
- Twilio number assignment from pool
- Chat onboarding service call to provision the user
- SMS via bot's own Twilio number with Windy Chat app download link
- Bot auto-joins user's DM room on chat.windypro.com
- Birth certificate generation (digital — immediate)
- Birth certificate printing + mailing (physical — queued)

**For Windy Mail:**
- Mail infrastructure (Postfix/Dovecot or managed service like Mailcow)
- Domain setup (windymail.ai — MX, SPF, DKIM, DMARC)
- Account provisioning API
- Rate limiting engine
- Reputation scoring system
- IMAP/SMTP access per account
- Webmail interface (optional, later)

**For Eternitas:**
- Separate incorporation (foundation or nonprofit)
- Registration API
- Identity verification pipeline
- Credential issuance (cryptographic)
- Public registry / lookup API
- Revocation mechanism with cascade (revoke → kill email, phone, chat)
- Birth certificate template and generation system

**Bottom line:** Windy Chat and Windy Mail are the moat. Eternitas is the trust layer. The birth certificate is the viral artifact. Keep them all in Windy Fly. Let HiFly be the open engine that makes people WANT to use the ecosystem.

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
- Products that are explicitly AI-native use `.ai` domains (Windy Fly, Windy Mail, Eternitas)
- Products that are traditional software use `.com` domains (Windy Word, Windy Chat, Windy Traveler, etc.)

### Domain Registry

| Product | Domain | TLD Rationale |
|---------|--------|---------------|
| Windy Word | windyword.com | Traditional software product |
| Windy Traveler | windytraveler.com | Traditional software product |
| Windy Chat | windychat.com | Traditional software product |
| Windy Mail | windymail.ai | AI-native email, signals agent-friendly |
| HiFly | hifly.ai | AI agent framework |
| Windy Fly | windyfly.ai | AI agent product |
| Windy Clone | windyclone.com | Traditional software product |
| Windy Cloud | windycloud.com | Traditional infrastructure product |
| Eternitas | eternitas.ai | AI bot registry |

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
- **2026-03-28:** HiFly/Windy Fly fork strategy defined. "IT'S ALIVE!" ceremony hardcoded as core HiFly DNA. Windy Chat designated as ecosystem-exclusive moat (not in HiFly core). Eternitas bot registry concept defined (independent entity). Windy Mail email product defined (windymail.ai). Birth certificate concept defined. Social layer folded into Windy Chat.
- **TBD:** Eternitas incorporation (foundation/nonprofit)
- **TBD:** Domain purchases (windymail.ai, eternitas.ai), website launches, app store listings updated
- **TBD:** HiFly open-source release (after Windy Fly proves the concept)

---

_This document is the canonical reference for all branding, naming, and product family decisions. All AG tabs, Kit clones, and developers should read this before doing any branding-related work._

_This document lives in: `windy-pro/`, `windy-pro-mobile/`, and `windy-agent/` repos._
