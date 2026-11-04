- **The site builder can take the agent's own short-lived pass (off for now).** With `WINDY_CODE_EPT_AGENT=1`, Windy Fly
  signs in to the Windy Code builder with its own EPT+agent for `windy-code` and a fresh proof on every request, and
  retries once when the builder asks for a new proof or a new pass. Until the builder turns its side on, nothing
  changes: the old pass is used, as today. A locked identity is never retried with the old pass.
