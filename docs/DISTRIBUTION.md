# Distribution Tiers — which install path is for whom

> Decision record, 2026-07-04 (Sprint 4). The audit found the wheel
> was silently NOT the product: `pip install windyfly` ships the
> Python brain only — the Bun/TypeScript gateway (dashboard, browser
> setup, WebSocket chat, remote hatch) exists only in a source
> checkout, and nothing said so. This document makes the tiers
> explicit instead of accidental.

## Tier 1 — Docker (OFFICIAL consumer path)

```bash
docker compose up -d
```

The `Dockerfile` + `docker-compose.yml` at the repo root ship the
complete product: brain + gateway + dashboard, pinned dependencies,
no Python/Bun/uv on the host. This is the path consumer-facing docs
should point at, the path the HiFly README should lead with, and the
only tier where "it works" is reproducible enough for a grandma
fleet.

- State lives in mounted volumes (`data/`, `~/.windy`), so upgrades
  are `docker compose pull && docker compose up -d` and rollbacks are
  re-pinning the previous image tag.
- The update-safety machinery (rollback history, post-update
  verification — `windyfly/update.py`) applies to in-place pip
  updates; under Docker, the image tag IS the version pin.

## Tier 2 — Source checkout + `windy go` (developer / fleet path)

```bash
git clone https://github.com/sneakyfree/windy-agent && cd windy-agent
windy go
```

Full product, hot-editable. `windy go` bootstraps uv + Bun and starts
brain + gateway. This is how the Windy fleet runs today (systemd units
pointing at checkouts) and how contributors work. Not for normies:
requires git, a toolchain, and reading error messages.

## Tier 3 — `pip install windyfly` (headless CLI, EYES OPEN)

> **⚠️ 2026-08-11: the published wheel is stale, and the gap is now an
> identity defect rather than a packaging one.** PyPI `windyfly` is
> **0.6.1, uploaded 2026-07-06** — verified against the PyPI JSON API.
> It predates the adopt-don't-mint guard (#345) and the terminal door's
> move onto the consumer endpoint (#349), both merged 2026-07-31. The
> published `hatch_orchestrator.py` contains no `preallocated` branch at
> all and still hatches via `/bots/register`, so Tier 3 today can mint a
> **second passport** for an agent that already holds one, and 401s on a
> fresh machine with no operator key. Until a release ≥ 0.7.0 ships,
> Tier 3 is a **source-checkout-only** tier in practice. Publishing that
> release is the fix; nothing in the repo can substitute for it.

The wheel packages `src/windyfly` only. You get: the brain, every
channel adapter, the CLI, memory, skills, recovery. You do NOT get:
the dashboard, the browser setup wizard, WebSocket chat, or remote
hatch — `windy setup`/`windy start`'s gateway step will tell you it
needs a source checkout.

Legitimate uses: embedding the brain in another system, running a
pure-Telegram/Discord agent on a tiny VPS, CI. If you want the
product, use Tier 1.

## The rule

**Anything user-facing (windyfly.ai, HiFly README, ballroom demos)
points at Tier 1.** Tier 2 is documented for developers. Tier 3's
limitation is stated wherever the pip install is mentioned. No tier
pretends to be another.
