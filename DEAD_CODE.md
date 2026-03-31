# Dead Code Audit

## Orphaned Python Files (8 files)

Never imported by any other source file in `src/windyfly/`:

| File | Purpose | Notes |
|------|---------|-------|
| `src/windyfly/mail_rate_limiter.py` | Mail spam-prevention rate limiter | Has a test file but no production consumer |
| `src/windyfly/birth_certificate_mailer.py` | Physical mail via Lob.com API | Never imported |
| `src/windyfly/integrations/windy_word.py` | Voice recording search | Stub, never wired in |
| `src/windyfly/integrations/windy_cloud.py` | Backup/sync client | Stub, never wired in |
| `src/windyfly/integrations/contact_discovery.py` | Contact hash matching | Stub, never wired in |
| `src/windyfly/integrations/windy_traveler.py` | Translation integration | Stub, never wired in |
| `src/windyfly/integrations/windy_clone.py` | Clone training status | Stub, never wired in |
| `src/windyfly/integrations/push_gateway.py` | Push notification client | Stub, never wired in |

**Note:** The entire `integrations/` package is dead. `__init__.py` has only a docstring — no imports from any submodule. The equivalent functionality lives in `tools/windy_api.py` (which IS wired in).

## Dead Test Files (1)

| File | Tests orphaned module |
|------|----------------------|
| `tests/test_mail_rate_limiter.py` | Tests `mail_rate_limiter.py` (orphan) |

## Orphaned Python-side IPC Handlers (6)

In `bridge/uds_server.py` dispatch table but never called by gateway (gateway handles providers locally in `providers.ts`):

| Handler | Method |
|---------|--------|
| `_handle_providers_list` | providers.list |
| `_handle_providers_update` | providers.update |
| `_handle_providers_add` | providers.add |
| `_handle_providers_remove` | providers.remove |
| `_handle_providers_set_model` | providers.set_model |
| `_handle_providers_set_key` | providers.set_key |

## Broken References (2)

| Location | Reference | Issue |
|----------|-----------|-------|
| `gateway/src/server.ts:934` | `config.reload` IPC method | No Python handler exists |
| `docs/research/greenfield_agent_architecture.md:28` | `gateway/run.py` | File doesn't exist (gateway is now TypeScript) |

## Undeployed Infrastructure (1)

`remote/` directory contains a Bun daemon for remote agent connections. Has `package.json` but no `node_modules` or `bun.lock` — dependencies never installed.

## All Clear

- **No broken Python imports** — all `from windyfly.*` resolve to existing modules
- **No stale config keys** — `windyfly.toml` keys map to real code paths
- **All gateway TS files** are actively imported
- **All test files** import existing modules (except the one orphan above)
- **scripts/install.sh** references valid commands
