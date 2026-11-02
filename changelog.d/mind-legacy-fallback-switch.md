- `WINDY_MIND_LEGACY_FALLBACK` (default on): set it to `0` and a failed EPT+agent mint or a refusal never falls back to
  the 365-day EPT; the request goes out with no credential and Mind answers 401. It turns off before Mind stops the
  legacy EPT on 2026-12-03. A test proves a model call still reaches Mind on EPT+agent with the long EPT ABSENT.
