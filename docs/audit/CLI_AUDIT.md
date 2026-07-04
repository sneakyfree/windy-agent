> **⚠️ POINT-IN-TIME SNAPSHOT (moved to docs/audit/ 2026-07-04).**
> Findings here reflect the repo as of the audit date in the text —
> several are already fixed. Verify against current code before acting.
> The current architectural assessment is the 2026-07-04 Fable audit
> (see CHANGELOG 0.6.0 + Sprint 1/2 PRs #231-#239).

# CLI Audit

## Command Results

| Command | Status | Exit Code | Notes |
|---------|--------|-----------|-------|
| `windy --help` | WORKS | 0 | Clean argparse help, 14 subcommands listed |
| `windy status` | WORKS | 0 | Rich tree with agent state; handles missing DB/config gracefully |
| `windy doctor` | WORKS | 0 | 7-category diagnostics; correctly identifies 2 warnings |
| `windy test` | WORKS | 1 | Self-test catches 401 from bad API key, reports 0/4 checks |
| `windy chat` | WORKS | — | Starts brain in CLI mode, presents `You:` prompt |
| `windy go` | WORKS | — | Detects existing config, asks "Launch Windy Fly?" |

**Invocation note:** `python -m windyfly` crashes — there is no `__main__.py`. Use `windy` (pyproject.toml script entry) or `uv run windy`.

## 14 Registered Subcommands

`go`, `init`, `setup`, `start`, `stop`, `restart`, `status`, `doctor`, `update`, `logs`, `config`, `version`, `chat`, `test`

All are wired to handler functions. No orphaned or unwired subcommands found.

## Issues Found

### 1. Missing `__main__.py`
`python -m windyfly` fails. A one-line file would fix it:
```python
from windyfly.cli import main; main()
```

### 2. Version Mismatch (3 different values)
- `pyproject.toml`: `0.0.1`
- `commands.py` VERSION constant: `0.1.0`
- `cli_status.py` fallback: `1.0.0`

### 3. Operator Precedence Bug in `_config_set`
`commands.py` line 641: `or` without parentheses creates unclear logic. Works by accident because `in_section` is checked in both branches.

### 4. No Timeout on SendGrid `urlopen`
`channels/email.py` WindyFlyEmail uses `urllib.request.urlopen` with no timeout — can hang indefinitely.

### 5. No Timeout on Twilio SMS `urlopen`
`channels/sms.py` uses `urllib.request.urlopen` with no timeout.

### 6. uv Deprecation Warning
Every command prints `warning: The tool.uv.dev-dependencies field ... is deprecated`. Fix: rename `[tool.uv] dev-dependencies` to `[dependency-groups] dev` in pyproject.toml.

## Error Handling Assessment

| Area | Grade | Notes |
|------|-------|-------|
| Missing config files | Good | Redirects to wizard |
| Missing API keys | Good | Doctor checks; test catches 401 with retry |
| Missing database | Good | Status shows "not found", no crash |
| Missing processes | Good | Stop handles missing PID/dead PIDs |
| Bad TOML syntax | Good | Doctor catches parse errors |
| Network errors | Good | Doctor uses timeouts and try/except |
| Ctrl+C in interactive commands | Good | KeyboardInterrupt caught |
