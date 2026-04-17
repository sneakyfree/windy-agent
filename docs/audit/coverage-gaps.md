# Coverage Gap Map — Windy Fly

Generated: 2026-04-17 (Wave 7 gap analysis)
Tool: `pytest --cov=src/windyfly` on 1113 passing tests.
Overall: **49 % line coverage** — 5,908 of 11,662 lines uncovered.

## Identity / auth / crypto files below 80 %

These are the ones that matter for the trust story. Each row is a P0 or
P1 in the main gap analysis.

| File | Coverage | What's untested |
|---|---|---|
| `eternitas/provision.py` | 56 % | `link_passport_with_identity` error paths, `_write_env`, synchronous wrapper, machine_id resolution |
| `eternitas/client.py` | 69 % | verify/update calls, response error branches |
| `trust/webhook.py` | 75 % | owner-notify path, rotation-on-change integration, unknown-event payloads |
| `matrix_provision.py` | 55 % | admin API fallback, registration token flow |
| `phone_provision.py` | 60 % | real Twilio path (only mock is tested) |
| `auth/audit.py` | (not listed — implicitly via contract tests) | file-permission chmod failure, concurrent writers |

## Zero-coverage modules (used in production)

| File | Why it matters |
|---|---|
| `main.py` (145 LoC, 0 %) | CLI entry point; any import-time bug = `windy go` fails |
| `vps_deploy.py` (170 LoC, 0 %) | `windy cloud vps-deploy` — the *paid path* — has no tests |
| `ecosystem_health.py` (77 LoC, 0 %) | `windy ecosystem` status check — the command users run when something breaks |

## Untested error paths (P1 each)

- `trust/check.py:_fetch` — 429 Retry-After path is logged but never exercised in tests
- `auth/bot_credentials.py:mint_bot_key` — 429 rate-limit from account-server
- `cloud_backup.py:backup_to_cloud` — checksum mismatch on restore, partial upload
- `hatch_orchestrator.py:_step_birth_certificate` — PDF generation failure mid-hatch
- `channels/email.py` — IMAP disconnect mid-fetch, OAuth refresh failure

## Mocked-but-should-be-integration-tested (P2 each)

- `mail_provision.py` — only mocked; no live JMAP probe test
- `matrix_provision.py` — mocked Synapse admin API calls
- `phone_provision.py` — Twilio path mocked; webhook callback never exercised end-to-end
- `cloud_backup.py` — Cloudflare R2 upload mocked; never tested against real S3 API
- `eternitas/client.py` — REST calls mocked; only *trust* is integration-tested against live Eternitas (see `tests/integration/test_trust_live.py`)

## CLI commands below 80 %

`commands/core.py` (30 %), `commands/ecosystem.py` (22 %), `quickstart.py`
(30 %), `setup_wizard.py` (21 %), `commands/_legacy.py` (27 %). These
are the user's first and most frequent touchpoints. Tests cover the
happy path but not keyboard interrupts, partial input, clipboard
failures, or typos.
