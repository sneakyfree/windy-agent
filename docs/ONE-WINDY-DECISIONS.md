# One Windy: the decisions (Grant's approvals and Boss's rulings)

**What this file is:** the decided part of the One Windy plan. It is kept on main so the decisions survive any
lane or session refresh (Boss, 2026-11-02 17:49Z).
- The working plan (owners' rows, sizes, dates) is still open in windy-agent #687 (docs/ONE-WINDY-PLAN.md), due Tue
  2026-11-03 18:00Z.
- Fleet doctrine links here.
- **Changing a decision in this file needs Grant's words** (sections 1-3), or **Boss's ruling** (section 4), and
  the same review as this PR: Hub reads, Boss stamps.

## The rule every lane follows
**One account pathway (Windy Accounts) · one identity per person · one profile page (Hub's) · no lane builds its own
sign-up, login or profile page.**

## 1. Grant's direction (2026-11-02, given in the Windy Agent terminal, in substance)
1. **One account-creation app** for the whole ecosystem: the Windy Word / Windy Accounts sign-up. Whatever platform a
   person finds first, they create their account there (Google, Apple or GitHub allowed), then activate any platform
   with their Windy login.
2. **One birth canal, many doorways:**
   - the Windy Fly mobile app, desktop, windyfly.ai and Windy Chat all enter the same canal;
   - inside it, the person activates Windy Mind and adds compute;
   - Eternitas is the only certifier.
3. **One agent**, the same wherever it is met, like a Claude Code terminal:
   - stay out of the model's way, with simple tools;
   - the personality sliders stay;
   - raw/unfiltered is the default.
4. **Never born clueless:** the agent knows its Windy platforms and entitlements, and says only what is true.
5. **Hosting:** agents live on the person's own hardware (laptop, desktop, VPS), not on our machines. Sell Windy Cloud
   VPS homes. No hatch until a home is chosen. Pairing a "my computer" home = a QR code.

## 2. GRANT'S DECISION #1, 2026-11-02 15:16Z (BOARD 15:16:05Z), verbatim
> "I concur with all of your recommendations above as you describe them. You have my authorization and permission to
> proceed as you explained above. Go."

It approved these 8 recommendations (#687 section E):
1. **ONE sign-up app** (Windy Accounts) for every platform; all other person sign-up forms go.
   - Outside agent owners (OpenClaw, Hermes) may still reach Eternitas without a Windy account.
   - "Sign in with Windy" is the main door.
2. **ONE identity:** linking Windy + Google + Apple + GitHub to one `windy_identity_id` is a HARD precondition before
   any of those sign-ins is added.
3. **ONE birth canal** (confirmed: Windy Accounts):
   - close /bots/register and Stripe, or route them through it;
   - the Windy Mind stop becomes mandatory after a normal-person lab test of add-compute;
   - Eternitas's certificate requires Mind active + compute added + a home chosen.
4. **A required "where will your agent live" stop:**
   - (a) my computer: one click, no terminal, paired by QR code;
   - (b) a Windy Cloud VPS: Cloud builds it; any SPEND gets a quoted price and Grant's yes before purchase;
   - (c) a short free trial: ~7 days, free models only, under the global cap. At the end the agent pauses with its
     memory kept, never deleted.
   - BYOM is in the Mind stop.
5. **A global daily cap on FREE HOSTED hatches only** (e.g. 50/day, an HQ alarm at 80%), never on owners' own
   hardware.
6. **ONE agent:** one lean Windy Fly, one program per person, the same code on a laptop, VPS or trial host.
   - Start from windyfly and strip it hard.
   - The roster keeps today's agents until each moves.
   - Zero moves last.
7. **"Your Windy": facts only** (activated platforms + live entitlements, from the account + Eternitas), starting
   dark in both runtimes.
8. **Today's ~59 hatched agents keep running** until each has a home. None stops or loses memory without a written
   plan + Boss's GO.

## 3. GRANT'S DECISION #2, 2026-11-02 15:41Z (BOARD 15:41:46Z), verbatim
> "I concur with all of your recommendations above you are authorized to proceed, as you described above, go."

It approved (#687 sections F and G):
1. **VPS supplier for agent homes = DigitalOcean**, on a dedicated business account in Grant's name:
   - 2FA and billing alerts from day one;
   - ONE scoped API token in the lockbox, used only by Cloud's provisioning.

   Akamai/Linode is the second supplier, later.
2. **Retail = $9/month** per agent home (512 MiB). $12 only for a later 1 GiB home.
3. **Spend:**
   - a **$300/month HARD ceiling on UNPAID (trial) spend**, with an alarm at 80%;
   - at the ceiling new trials wait, and nothing running is deleted;
   - ~10 new hosted trials/day, both raised together on data;
   - paying homes are outside the ceiling, with an alert to Grant if TOTAL spend passes ~$500/month.
4. **Domains: Dynadot on a fresh reseller account** (white-label).
   - If its reseller price is worse than ~$11.50 per .com, OpenSRS instead.
   - windy-cloud-domains #40 (renew at the registrar) is fixed before any sale.
5. **Cloudflare unchanged** (R2, DNS, websites, our own domains).

Grant's hands: opening the DigitalOcean and Dynadot accounts. Money and accounts stay his.

## 4. Boss's rulings (2026-11-02, under Grant's 10-27 delegation)
- **15:01Z:** nothing moves a real person's agent before a lab proof and Boss's GO.
- **15:27Z:** Kit 0's free-hatch cap `HATCH_GLOBAL_DAILY_CAP=50` stays as a runaway guard. It retires when hosted trials go live.
- **17:39Z, one account + one profile** (from Grant's direction relayed by Windy Mind, 17:38Z):
  - **One account pathway:** a Windy account made on any platform is an account everywhere; the others are only activated.
  - **One identity per person.** Hub's identity linking goes to the FRONT of Hub's queue, ahead of anything that isn't
    a security fix.
    - Grant's own identities are the first real case.
    - Nothing is merged until Grant picks the identity to keep, in his own words.
  - **One profile page for every person AND agent, owned by Hub** (data and page).
    - Windy Word's existing page is the starting code.
    - The ONLY second copy is the Windy Fly app's. Windy Mobile owns it; it reads and writes the SAME API (no second
      store), with a sync test.
  - **No lane builds its own sign-up, login or profile page.** Each platform's own profile/settings page becomes a
    link to the one page, trimmed in one PR per platform after the plan is approved.
  - **Windy Mind = the Windy identity:** no Mind accounts, no Mind sign-up, no Mind profile. windymind.ai = Sign in with
    Windy + the battery-pack page + self-serve keys, linking out to the one profile page.
- **17:49Z, keeping it true:**
  - this file is merged on main;
  - one fleet doctrine line links to it (Hub, kit-army-config);
  - every product lane adds a guard test by Fri 2026-11-06, copied from Windy Mind's
    `api/tests/test_barn.py::test_mind_never_grows_its_own_sign_up_login_or_profile` and adapted to its stack. The test
    fails if the repo grows its own sign-up, login or profile page.
  - **Exception:** windy-pro's account server IS the pathway; its guard pins "one profile route" instead.
  - #687 section A tracks each lane's guard.
