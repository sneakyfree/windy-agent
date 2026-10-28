- **vault.call_api (dark, lab only).** One https call with a key the owner shared, never shown to the model
  (Vault everywhere item 3; design rev 4; contract vault-tools.v1 1.0.1). Behind `WINDY_VAULT_CALL_API=1` on top of
  `WINDY_VAULT_TOOLS=1`, OWNER band. The request's origin must be in the lease's owner-set `targets` (agent-routes
  1.5.0, vendored); the key goes through the pinned egress and every form of it is scrubbed from the answer; writes
  answer `needs_owner_ok` and never run until the owner's confirm screen exists; 30 calls/hour per item; the audit
  row holds the method and host only. The redactor also masks a key's JSON form with non-ASCII left as-is or with
  upper-case `\uXXXX` escapes.
