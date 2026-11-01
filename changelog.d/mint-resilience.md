- Mode-B tokens survive a network blip (Zero 2026-11-01: ~126 short-token mints died before reaching Eternitas,
  and each Mind call fell back to the 365-day EPT). A cached EPT+agent is refreshed from 90 s before expiry; a
  TRANSIENT refresh failure (network, 5xx, 429) keeps the still-valid cached token; with nothing cached a network
  failure is retried once; any other refusal (revoked, suspended...) drops the cached token. The warning now names
  the error class. Every Mind request sent with the legacy EPT is counted on service.health as `mind_legacy_auth`
  (sent at 0 too), which HQ's zero_legacy_ept_fallback alarm reads.
