# vault.call_api: design (Vault everywhere item 3), revision 3

Revision 2 folded in Hub's review (2026-10-27); revision 3 adopts Windy Vault's V7.6 c2e for host bindings. It builds on Vault plan V7.6: c2 (a lease value reaches only the
tool, never the model) and c2b (the redactor returns with the first lease-using tool, which is this one). Dark
behind `WINDY_VAULT_TOOLS`, OWNER band, lab only (zero-lab + Vault sandbox). The params, enums and say-states go
into Hub's shared windy-contracts tool doc, so Windy Fly and the roster use one definition.

## The tool

`vault.call_api {item_id, method: GET|POST|PUT|PATCH|DELETE, url, auth: bearer|header|basic, header_name?, username?, query?, body?}`
returns `{ok, status, headers (allow-listed), body (redacted, capped), truncated}` or a fixed-word refusal or
`needs_owner_ok {card_id}`. Audit row: host + method + status only. Never the path, query, body or a value.

## Guards (all in code; the model writes none of the text a person approves)

**G1 Allowed destinations live on the ITEM, in the Vault (Vault plan V7.6 c2e).** Each item has `targets`, a list
of normalized https origins (the same FillTarget type as phone-secrets' fill targets: lowercase host, :443 implied,
no path, no subdomain wildcard). Only the OWNER sets them, on an owner route with a fresh sign-in, audited. No agent
route can add or change one. The lease answer carries `targets` with the value every time, and the tool checks the
request's origin against that list in code. Empty or missing `targets` means call_api refuses that item (fail
closed); ordinary leases are unaffected. There is no agent-initiated "add a host" request in v1 (a host would be
agent-written text, which conflicts with enums-only, KEEP 5). The tool instead says in plain words: "To let me use
this key with <host>, add it to the key in your Windy Vault." A card may come later; its words go to Grant first.
Nothing is stored where the model could write it. Origin match: strict URL parse, IDN to punycode, trailing dot
stripped, exact origin compare. A URL with userinfo (`https://a.com@b.com`) is refused. Until the Vault serves
`targets`, call_api stays dark; for zero-lab only, a clearly marked test constant may stand in, removed when the
field lands.

**G2 Transport.** https only. Resolve once and CONNECT to that pinned IP, with SNI/Host still the name (defeats
DNS rebinding). Refuse loopback, private, link-local, 0.0.0.0/8, 100.64/10, fc00::/7, IPv4-mapped IPv6 and
multicast. No redirects followed. HTTP(S)_PROXY env vars ignored. A vault value containing CR or LF is refused
(header injection). Timeout 20 s; URL ≤ 2 KiB; request body ≤ 64 KiB.

**G3 Redactor (c2b, revived for this tool).** Before the call, register the value in every form a server can echo: raw, hex (both cases),
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

- **Windy Vault:** V7.6 c2e: `Item.targets` (owner route, fresh sign-in), `lease.targets` (agent-routes minor) and
  the shared FillTarget type. Plan codon + contract draft to Hub this week; it ships dark in the next sandbox release.
  The redactor (c2b, DEFERRED by Boss 10-07, #457) comes back with this tool: stated in the call_api GO ask so the
  plan is aligned, with Vault updating c2b's text.
- **OPEN (Hub + Vault):** G4's write card shows method + host + path from the exact request. The host is from
  `targets` (owner-set), but the PATH is request text. Hub and Vault: is a code-built, hash-bound card that shows
  the request's path compatible with KEEP 5, or should v1 writes ask in another way?
- **Windy Hub + Mobile + desktop:** the two cards (words to Grant first).
- **Windy Agent:** the tool, G2/G3/G4 in the Windy Fly harness, and the echo-server tests. Coded against Vault's
  contract once Vault fixes it.
