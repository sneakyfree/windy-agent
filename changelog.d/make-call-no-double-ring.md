- `make_call` never rings the owner twice (Hub on #609, Boss 11-01). After a call that may have rung (placed,
  status unknown, or windy-text's "may have dialled" 502) it refuses a second call for 10 minutes, in code and shared
  across the agent's processes; a clean refusal before any dial doesn't count. Each call sends an Idempotency-Key
  derived from the passport and the message, so windy-text #124 drops a repeat server-side. The tool description
  adds: "If the status is unknown, do not call again: tell your owner a call may have been placed."
