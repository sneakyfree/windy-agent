- Chat session on mode B, dark (`WINDY_CHAT_EPT_AGENT=1`): the agent's Matrix session is minted with an EPT+agent for aud
  `windy-chat` plus a DPoP proof for that exact URL. A refused one (401/403) or a failed mint falls back to the
  365-day EPT until 2026-12-15.
