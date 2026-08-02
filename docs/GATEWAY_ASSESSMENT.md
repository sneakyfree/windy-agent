# `gateway/` — what it is, what it isn't, and what deploying it would cost

**Written 2026-08-02 by the windy-agent instance**, at the request of
`eternitas/docs/hatch-consolidation/05-windy-agent-CLEANUP.md` TASK 2.

**This document does not deploy anything and does not recommend deploying
anything.** Whether a managed always-on agent host should exist is Grant's
call — it is a recurring bill and a support surface, not just code. This is
the missing input to that decision.

Every number below was measured on 2026-08-02. Claims are marked **MEASURED**
(I ran it or read it off a live system) or **READ** (from the code).

---

## The one-paragraph answer

`gateway/` is a Bun HTTP + WebSocket server that fronts a single machine's
Windy Fly agent: a dashboard, a chat bridge, and a `POST /hatch/remote`
endpoint that runs a hatch on **the machine the gateway is running on**. It
was built for the user's own laptop, which is why windy-pro's
`WINDY_AGENT_URL` defaulted to `http://localhost:3000`. It works, and it has
never been deployed anywhere.

**The critical correction to how everyone has been describing it: deploying
`gateway/` would NOT give browser users a live agent.** `/hatch/remote`
spawns a hatch, streams the ceremony, and the subprocess **exits**. It
provisions an identity and writes local files; it does not leave anything
running. An always-on managed agent is a substantially larger thing that does
not exist in any repo.

---

## What it actually is

| | |
|---|---|
| Runtime | Bun (TypeScript) |
| Size | 39 files, ~4,000 lines across `src/*.ts`, 980 KB incl. dashboard |
| Endpoints | dashboard + auth, chat bridge, WebSocket, `/api/health`, `/api/webhooks/trust`, `POST /hatch/remote` |
| Referenced by | **nothing** — no CI workflow, no Dockerfile, no compose file, no Makefile target, not `pyproject.toml`, no root `package.json` (MEASURED) |
| Test suite | `gateway/tests/` exists (not run here — `bun` is not installed on this Mac) |

### Correction to the brief's staleness figure

`05-windy-agent-CLEANUP.md` says *"Every file is stamped `Jul 20 22:22`"* and
treats it as 13 days untouched. That is the **filesystem mtime** — an artifact
of when the working copy was written, not of authorship.

**Git says the last substantive commit touching `gateway/` was 2026-05-29**
(`68b23b9`, the dashboard e2e QA pass). MEASURED.

So it is **~65 days stale, not 13.** Five times older than assumed. That
strengthens rather than weakens the case for treating a deployment as new
work rather than as flipping a switch.

## What it is NOT — and please don't "consolidate" it

`gateway/src/hatch-remote.ts` is **transport, not orchestration**. It accepts
the handoff from windy-pro, spawns `python -m windyfly.hatch_remote`, and
forwards each JSON line as an SSE frame. Every decision about what a hatch
*does* lives in `src/windyfly/hatch_orchestrator.py`.

There is no second hallway here and nothing to merge. A note to this effect is
now in the file's own header so the next instance doesn't spend a session
discovering it.

---

## The blocker nobody has written down: it is single-tenant by construction

`hatch-remote.ts` spawns every hatch with `cwd: projectRoot` and no per-hatch
`WINDYFLY_HOME`. **Every hatch on a given gateway shares one project root.**
READ, then MEASURED.

I ran two hatches — "alice" and "bob", each with their own real Eternitas
passport — into one project root:

```
alice passport: ET26-WBW1-P6TJ
bob   passport: ET26-9B3U-E52X

data/provision_recovery.json  (the ONLY file both wrote):
    "agent_name": "bobs Agent",
    "passport_id": "ET26-9B3U-E52X"
```

**Bob's hatch silently overwrote Alice's recovery record.** That file is what
the retry path uses to heal a partial hatch, so on a shared host only the most
recent hatch is ever recoverable. Alice's failed mail/chat/phone steps become
permanently unhealable, and nothing reports this.

Two honest caveats, because I checked and want the record straight:

- I expected `.env` collisions too (the passport + EPT are written there).
  **That did not reproduce** on the handoff path — the EPT write is
  conditional and the verify endpoint returns no token. I'm reporting the one
  collision I actually observed, not the one I predicted.
- With mail/chat/phone credentials configured, more shared state would be in
  play than this credential-less test could exercise. The recovery-file
  collision is the floor, not the ceiling.

On a single-user laptop — the design intent — sharing one project root is
exactly right. On a multi-tenant host it is wrong, and it is not a small fix:
per-hatch isolation means a home directory, database, and `.env` per user, and
every helper anchored to `get_project_root()` has to learn about that.

---

## What deploying it would take

Ordered by how likely each is to surprise you.

1. **Per-tenant isolation** (above). This is the real work. Not a config
   change — it touches `platform.get_project_root()` and every caller.
2. **A host with Bun.** Windy 0 does **not** have Bun installed (MEASURED). It
   also needs `uv`, Python, and the `windyfly` package installed at the
   project root, because the gateway shells out to it.
3. **Secrets wiring.** The gateway reads 14 env vars (MEASURED). The
   load-bearing ones: `BROKER_HMAC_SECRET` / `WINDY_BROKER_SIGNING_SECRET`
   (without them `/hatch/remote` 401s every request), `WINDY_PRO_URL`,
   `DASHBOARD_PASSWORD`, `GATEWAY_PORT`.
4. **Public exposure.** `/hatch/remote` is deliberately **exempt from
   dashboard auth** (READ — `server.ts:451`), on the reasoning that the
   `broker_token` in the body is itself the authorization. That is defensible
   for a loopback endpoint on a laptop. Publishing it to the internet makes
   that broker-token check the *only* thing standing between the world and a
   process spawn on your server. It needs a real review before it faces the
   public, not an assumption.
5. **Concurrency ceilings are laptop-shaped**: `MAX_CONCURRENT_HATCHES=3`,
   `MAX_HATCHES_PER_IP=2` (MEASURED). Note the per-IP cap collapses to a
   single shared bucket behind a CDN or proxy, exactly the trap Eternitas
   already documented for its own rate limiter.
6. **CI, image, deploy config, rollback** — none exist for `gateway/`.

## What running it would cost

Two different questions, and conflating them is where the "managed gateway"
idea gets expensive.

**(a) Hatching only** — what `gateway/` does today. Each hatch is a
short-lived Python process, seconds long, then it exits. A small VPS handles
this; the cost is one more always-on box, not a per-user cost. This is cheap.

**(b) Always-on agents** — what people actually mean by "managed Windy Fly."
This does not exist yet in any repo. The cost basis, measured from the live
agent on Windy 0:

| Measured on Windy 0 | Value |
|---|---|
| One live windy-agent process | **728 MB RSS**, 0.6% CPU idle |
| Host | 64 GB RAM, 8 cores |
| Currently used / available | 12.8 GB / 51.4 GB |

At ~730 MB per resident agent, a 64 GB host holds roughly **70 always-on
agents** with headroom — before any LLM inference, which is billed separately
and is the larger variable cost.

For scale: four bootcamps at ~25 attendees each is ~100 agents, i.e. **two
such hosts minimum**, plus the isolation work in (1), plus someone on call
when a stranger's agent wedges at 2am. That last item is the one that doesn't
show up on an invoice and is usually the real cost.

---

## What I'd say if asked

Not my decision, and I'm not making it. But the input Grant asked for:

**The cheap path already works.** Since 2026-07-31 (#349), `windy go` hatches
correctly on a fresh machine with no operator key — that path was 401-broken
before, which is the only reason "the user can start the agent later" was ever
a hollow promise. It isn't now. windy-pro's own code already treats the
`/hatch/remote` step as optional, in a comment: *"the user keeps their
passport + credentials; the agent process can be started later."*

So the browser hatch is not broken today; it issues a real identity, and the
agent runs where doctrine #2 says it belongs — on the human's own machine.

**Deploying `gateway/` would not close the gap people think it closes** (it
leaves no agent running), and doing the thing that *would* close it means
building per-tenant isolation and an always-on host, six weeks before the
bootcamps, as a brand-new production service. Doctrine #8 — stability first,
capability last — argues loudly against that timing.

If a managed option is wanted later, the per-user VPS path
(`windy cloud vps-deploy`, already in `src/windyfly/vps_deploy.py`) gets there
without making Grant the landlord of everyone's agent, which is also the
friendlier answer to doctrine #2.
