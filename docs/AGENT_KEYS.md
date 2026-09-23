# Agent signing keys (Eternitas agent-keys v1)

A passport number proves that a passport exists. It does not prove that the
caller holds it. Since 0.7.3, every hatched Windy Agent has its own **ES256
(P-256) key pair**:

- The **private key** is generated on the machine the agent runs on and never
  leaves it. It is not sent to Eternitas, not written to any log, and not put
  in the cloud backup (the backup ships only `data/windyfly.db`).
- Eternitas stores only the **public JWK**. Anyone can check what the agent
  signs against `GET /api/v1/bots/{passport}/keys`. Windy Drops signed publish
  is the first consumer.

Spec: windy-orchestra `specs/eternitas-agent-keys.v1.md`. Code:
`src/windyfly/eternitas/agent_keys.py`.

## Where the key lives

One file per **agent**, mode `0600`:

| Setting | Path |
|---|---|
| `WINDY_CREDENTIALS_FILE` | this path. Use it on machines with more than one agent, and for services. |
| otherwise | `<state dir>/credentials.json` (`WINDY_STATE_DIR` or `~/.windy`) |

```json
{"eternitas": {
  "private_key": "<PEM of the ACTIVE key>",
  "kid": "<RFC 7638 thumbprint>",
  "keys": [{"kid": "…", "private_key": "…", "status": "active|retiring",
            "created_at": "…", "registered_at": "…|null"}]}}
```

Other top-level keys in the file are left untouched. Every write is atomic
(temp file, then rename). The previous version is kept next to it as
`credentials.json.bak-<UTC stamp>` (also 0600), and those backups stay on the
machine. `kid` is the RFC 7638 JWK thumbprint of the public key.

A restore onto a new machine therefore gets a **new** key, registered
automatically. Revoke the old one with `windy agent-key reset`.

## What happens automatically

| When | What |
|---|---|
| Boot (`eternitas.agent_keys` step, background thread) | If there is no key, generate one and save it. If it isn't registered, register it (challenge → `POST /bots/{p}/keys` with the agent's own EPT and a proof-of-possession JWS). |
| Daily (`eternitas.agent_keys.daily`) | Same check. Also finishes a rotation whose retire step failed. |
| `windy login`, and the end of a successful hatch | The same, run synchronously and best effort. |

It never blocks or fails boot. If Eternitas doesn't have the agent-keys
routes yet (a bare `404 Not Found`), it logs **one** INFO line, "agent keys
not yet supported by Eternitas", and tries again the next day. The key made
at the first attempt is kept, and that same key is registered once Eternitas
ships.

Background registration only ever uses the agent's **own** EPT. If the EPT has
lapsed, nothing is sent: run `windy login`, then `windy ept refresh` (or
`windy agent-key reset`). All automatic key work is off under pytest and when
`WINDY_DISABLE_AGENT_KEYS` is set.

## Commands

`windy keys` already manages the wk_ bot credential, so these commands live
under `windy agent-key`:

```
windy agent-key status           # local keys, which is active, what Eternitas lists
windy agent-key rotate           # register a new key, retire the old one (overlap ≤ 2)
windy agent-key reset            # OWNER recovery (see below)             [y/N]
windy agent-key revoke --kid K   # revoke one key; everything it signed turns invalid  [y/N]
```

None of them print key material, only kids.

### Recovery: `windy agent-key reset`

Use it when the key is lost (a new machine, a deleted file) or when you
suspect it has leaked.

1. It opens a **fresh** browser sign-in with `prompt=login` and `max_age=0`.
   Eternitas accepts an owner-registered key only if the hub token has
   `email_verified: true` and an `auth_time` less than 10 minutes old, and a
   stored or refreshed session never meets that. The new token is used once
   and is not saved over your `windy login` session.
2. It registers a brand-new key with that token and a PoP. Eternitas flags the
   key `registered_via: owner_recovery`, tells every platform, and emails you.
3. It revokes every other key Eternitas lists as active, including one lost
   with an old device, plus any key still in the local file.

The new key is registered **before** the old ones are revoked, so a refused
reset leaves you with the key you had. Eternitas allows one reset per passport
per 24 hours. A second one gets "an owner reset is allowed once per 24h per
passport".

## Signing an artifact

```python
from windyfly.eternitas.agent_keys import sign_artifact
jws = sign_artifact(payload_bytes)   # detached compact JWS
```

The protected header is `{alg: ES256, typ: eternitas-sig+jws, kid, passport,
signed_at}`, and the signature binds both `kid` and `passport`. A verifier
accepts a **new** artifact only while its key is `active` at verification
time.

The module also has helpers for platform proof-of-possession (`platform_pop`,
mode A, `typ eternitas-pop+jwt`, `exp ≤ iat+60`) and for RFC 9449 DPoP proofs
(`dpop_proof`). Mode B token exchange is not wired up yet (Eternitas step 4).

## Wire details windy-agent assumes

The spec leaves these open; windy-agent assumes the following and will follow
Eternitas if it differs:

- Challenge: `POST /bots/{p}/keys/challenge` → `{"nonce": …}`.
- Register body: `{"jwk": <public JWK + kid/alg/use>, "proof": <compact JWS>}`.
  The proof header is `{alg, typ: JWT, kid, passport}` and its payload is
  `{passport, nonce, iat}`.
- Retire and revoke: `POST …/keys/{kid}/retire` and `POST …/keys/{kid}/revoke
  {"reason"}`. A 404 or 409 on either counts as already done.
- "Not deployed" means a 404 whose detail is exactly `Not Found` (FastAPI's
  catch-all). Any other 404, such as an unknown passport, is a real failure.
