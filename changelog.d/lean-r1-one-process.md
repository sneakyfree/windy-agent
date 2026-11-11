- **Your agent is one program now.** `windy start` starts one process, and it answers in Windy Chat. The local web
  dashboard and the gateway on port 3000 are removed, with everything that only they could do: the browser chat page,
  the setup wizard page, the slider and memory pages, personality rollback, skill promote and rollback, the conflict
  and decay buttons, the journal, moments and failures lists, and the report card. Nothing opens a port on your
  computer any more.
- **You no longer need Bun.** The installer, `windy doctor` and the setup wizard stop asking for it. `windy doctor`
  shows one line for the agent process, and reads it correctly (it used to say "not running" for a running agent).
- **Old service files keep working.** A systemd or launchd unit written before this release still starts
  `windyfly.bridge.uds_server`; that name now starts the same one process and says so in the log. New units start
  `windyfly.main --channel matrix`.
- **The container runs the one process.** No port 3000 and no HTTP health check; `docker compose up -d --build`
  builds and starts it as before.
- **Your agent asks Eternitas for its standing; Eternitas no longer pushes it.** The push receiver sat on the
  removed gateway. The agent's own check every 5 minutes stays.
- **The `windy-mcp-server` command is removed** and its contract `contracts/control.mcp.v1.json` is marked retired.
  It was not started by anything and had no caller.
- Removed with them: `/audit-log` and `/budget breakdown` (both only pointed at the dashboard), `windy logs gateway`,
  `scripts/smoke-test.sh` (use `windy test`), `docs/API_REFERENCE.md` (the gateway's routes).
