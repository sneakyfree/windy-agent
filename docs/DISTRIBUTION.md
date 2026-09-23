# Distribution Tiers — which install path is for whom

> Decision record, 2026-07-04 (Sprint 4). The audit found the wheel
> was silently NOT the product: `pip install windyfly` ships the
> Python brain only — the Bun/TypeScript gateway (dashboard, browser
> setup, WebSocket chat, remote hatch) exists only in a source
> checkout, and nothing said so. This document makes the tiers
> explicit instead of accidental.

## Status, 2026-09-23 — read this first

- **Official release channel: PyPI** (`pip install windyfly`, 0.7.1+).
  That's what gets versioned, tagged and published.
- **No Docker image has ever been published.** `ghcr.io/sneakyfree/windy-fly`
  does not exist: the release workflow targets billing-locked GitHub-hosted
  runners, and no credential with `write:packages` is set up. Earlier
  versions of this page called Docker the "official consumer path" and
  described `docker compose pull` upgrades. That was never true, so it's
  removed until an image actually ships.
- Docker still works as a **build-it-yourself** option from a source
  checkout (CI builds the image on every PR).

## Docker — build it yourself (coming soon as a published image)

```bash
git clone https://github.com/sneakyfree/windy-agent && cd windy-agent
docker compose up -d --build
```

The `Dockerfile` + `docker-compose.yml` build the complete product
(brain + gateway + dashboard) with pinned dependencies and no
Python/Bun/uv on the host. When a published image exists, this section
will switch to `docker compose pull`.

- State lives in mounted volumes (`data/`, `~/.windy`). To upgrade:
  `git pull && docker compose up -d --build`.
- The update-safety machinery (`windyfly/update.py`) applies to pip
  installs. Under Docker, your checkout's commit is the version pin.

## Tier 2 — Source checkout + `windy go` (developer / fleet path)

```bash
git clone https://github.com/sneakyfree/windy-agent && cd windy-agent
windy go
```

Full product, hot-editable. `windy go` bootstraps uv + Bun and starts
brain + gateway. This is how the Windy fleet runs today (systemd units
pointing at checkouts) and how contributors work. Not for normies:
requires git, a toolchain, and reading error messages.

## Tier 3 — `pip install windyfly` (official release; headless CLI)

> **Resolved 2026-09-12 by the 0.7.0 release.** From 2026-08-11 this
> tier carried an identity defect, not just a packaging lag: the
> published 0.6.1 wheel (uploaded 2026-07-06) predated the
> adopt-don't-mint guard (#345) and the terminal door's move onto the
> consumer endpoint (#349), so Tier 3 could mint a **second passport**
> for an agent that already held one, and 401'd on a fresh machine with
> no operator key. 0.7.0 ships both fixes. Pin `windyfly>=0.7.0`; treat
> anything older as a source-checkout-only tier.

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
