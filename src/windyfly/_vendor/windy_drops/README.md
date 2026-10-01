# JWS conformance vectors (signed-drop verification)

`vectors.json` is the contract for any surface that installs a signed drop (Windy Fly skills, the Control Panel, ...). Each of the vectors is self-contained: a manifest, the registry's `bundle_sha256`, the signer passport's `keys` (the `/api/v1/bots/{passport}/keys` list, `null` = Eternitas does not know the passport), the revocation inputs, optional bundle bytes, and the expected `reason`. A consumer must reproduce every reason exactly.

- **Reference verifier:** `python/sdk/src/windy_drops/lib/verify.py` (`verify_signed_drop`), pure and offline. Tested in `python/sdk/tests/test_verify_vectors.py`.
- **Agreement with the registry:** the registry's own `signature_verify.verify_signature` was run over the same vectors (2026-10-01) and gives the same reason on every case except `bundle_sha_mismatch`, which is a consumer-side check (re-hash the downloaded bytes).
- **Provenance:** produced by the real SDK signer with the throwaway test key in `../test-keys` (never an agent key) via `make_vectors.py`. ECDSA is randomised, so regenerating changes the bytes: do it only on purpose, then re-vendor the file into every consumer and re-run their drift test.
- **What a consumer still owns:** fetching keys + the CRL + the suspension feed (fail closed if unreachable), refusing unsigned drops by default, its own minimum integrity band, and asking the owner before install. Authenticity is decided here; trust is not.

Reason codes (stable): `ok, bundle_sha_mismatch, no_signature, bad_algorithm, no_passport, no_jws, digest_mismatch, bad_jws_format, bad_header, passport_mismatch, bad_sig_length, passport_revoked, key_revoked, passport_suspended, unknown_passport, unknown_key, bad_key_type, kid_mismatch, key_not_active, bad_signature`.

## Band policy (separate from authenticity)
`verify_signed_drop` says whether a signature is genuine. Whether to TRUST that signer is the surface's policy. `check_min_band(band, minimum, allow_unproven=False)` plus `policy_vectors` pin the integrity-band gate, and the vector `valid_signature_band_below_minimum` is an authentic signature (band `poor`) that a surface with minimum `fair` must refuse (`band_below_minimum`).

- Bands ascend `critical < poor < fair < good < exceptional`. `unproven` is OFF the scale: a neutral standing, the band every newly hatched agent carries.
- **Trap:** a surface that gates on band refuses every brand-new agent, including a newly hatched first-party publisher (the planned "Windy Drops official" agent will be `unproven` at first). Decide `allow_unproven` on purpose.
- The band in a manifest is a SNAPSHOT at signing time. Prefer the signer's live band (registry drop detail / Eternitas).
- Policy reason codes: `ok, band_below_minimum, band_unproven, band_unknown`.
