# vault.call_api: design (Vault everywhere item 3), revision 2

Revision 2 folds in Hub's review (2026-10-27). It builds on Vault plan V7.6: c2 (a lease value reaches only the
tool, never the model) and c2b (the redactor returns with the first lease-using tool, which is this one). Dark
behind `WINDY_VAULT_TOOLS`, OWNER band, lab only (zero-lab + Vault sandbox). The params, enums and say-states go
into Hub's shared windy-contracts tool doc, so Windy Fly and the roster use one definition.

## The tool

`vault.call_api {item_id, method: GET|POST|PUT|PATCH|DELETE, url, auth: bearer|header|basic, header_name?, username?, query?, body?}`
returns `{ok, status, headers (allow-listed), body (redacted, capped), truncated}` or a fixed-word refusal or
`needs_owner_ok {card_id}`. Audit row: host + method + status only. Never the path, query, body or a value.

## Guards (all in code; the model writes none of the text a person approves)

**G1 Host binding lives in the Vault.** A key goes only to a host the OWNER approved for that item. Bindings are
stored server-side with the item, written only by the owner, and seen and revoked in the Vault UI. The tool reads
them on every call; nothing is kept where the model could write it. With no binding, the tool files a Vault
request and answers `needs_owner_ok`. The owner then sees a code-built APPROVAL CARD: the exact host, the
registrable domain in bold, and "first time this key goes to this host". No chat command can add a binding.
Host match: strict URL parse; compare the parsed hostname exactly (lower-case, IDN to punycode, trailing dot
stripped); an explicit port is part of the binding. A URL with userinfo (`https://a.com@b.com`) is refused.

**G2 Transport.** https only. Resolve once and CONNECT to that pinned IP, with SNI/Host still the name (defeats
DNS rebinding). Refuse loopback, private, link-local, 0.0.0.0/8, 100.64/10, fc00::/7, IPv4-mapped IPv6 and
multicast. No redirects followed. HTTP(S)_PROXY env vars ignored. A vault value containing CR or LF is refused
(header injection). Timeout 20 s; URL ≤ 2 KiB; request body ≤ 64 KiB.

**G3 Redactor.** Before the call, register the value in every form a server can echo: raw, hex (both cases),
base64, base64url, JSON-escaped, percent-encoded (upper and lower), base64 of `user:value` for basic auth, and
any run of 8 or more characters of the value (partial echoes like a key prefix plus "..."). Scrub AFTER
decompression (gzip/br): the response body, headers and error text, before anything reaches the model, a log, an
audit row or a stored episode. Release the lease in `finally`. Tests run against an echo server that reflects the
Authorization header and body in every encoding.

**G4 Writes ask the owner (principle #10).** DELETE always asks first. POST/PUT/PATCH ask first by default. The
ask is a Vault approval card showing method + host + path, hash-bound to the exact request. TAINT is sticky:
once untrusted content (a web fetch, an email body, a non-owner message) is in the context, every write goes to a
card until the session is cleared. "Allow writes to this host without asking" is a LATER owner opt-in; its words go
to Grant via Boss, and it's never a default (same rule as auto-save). GET with a bound host runs without a card.

**G5 Limits.** One lease per call; response capped at 64 KiB (`truncated`); no value in any exception message.

## Dependencies (owners)

- **Windy Vault:** server-side host bindings per item (owner-written; listed in `capabilities`), plus two new
  agent request classes with owner approval cards: `bind_host {item_id, host}` and `call {item_id, method, host,
  path, req_hash}`. These are contract changes in agent-routes / owner-api; their order is Vault's call.
- **Windy Hub + Mobile + desktop:** the two cards (words to Grant first).
- **Windy Agent:** the tool, G2/G3/G4 in the Windy Fly harness, and the echo-server tests. Coded against Vault's
  contract once Vault fixes it.
