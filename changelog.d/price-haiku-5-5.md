- **Haiku 5.5 calls get a real cost.** Windy Mind now serves Haiku requests with claude-haiku-5-5, which had no price, so
  its cost read as unknown. Its prices come from Anthropic's pricing page (read 2026-10-22), including the higher
  row for a request whose prompt (input plus cache reads and writes) is over 100,000 tokens.
