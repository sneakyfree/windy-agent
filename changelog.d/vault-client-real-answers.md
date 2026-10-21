- **The Vault client speaks Vault's real answers.**
  - vault-api reports every sign-in failure with one code, `unauthenticated`. The client now re-mints once on
    that code, then reports it; it never loops.
  - `vault_unavailable` carries Vault's back-off time to the caller.
  - A lease the agent can't open is given back before it asks again, so it doesn't count against the owner's
    budget.
  - A lease id is checked before it goes into an address.
