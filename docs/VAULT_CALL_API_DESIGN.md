# vault.call_api: design (Vault everywhere item 3), revision 4

Revision 2 folded in Hub's review (2026-10-27); revision 3 adopts Windy Vault's V7.6 c2e for host bindings;
revision 4 makes the redactor whole-forms-only (Hub on #655) and states the limits as built. It builds on Vault plan V7.6: c2 (a lease value reaches only the
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

**G3 Redactor (c2b, revived for this tool), WHOLE FORMS ONLY.** Before the call, register every COMPLETE form a
server can echo: raw, hex (both cases), base64 and base64url at byte offsets 0, 1 and 2 (unstable edge characters
trimmed, so the value is found inside a larger base64 blob at any alignment), JSON-escaped, percent-encoded (upper
and lower), and base64 of `user:value` for basic auth. Scrub AFTER decompression: the response body, every header
(a redirect's Location included) and error text, before anything reaches the model, a log, an audit row or a
stored episode. Release the lease in `finally`.
*Why whole forms only:* masking partial runs of a value (an earlier draft masked every 8-character window) is an
ORACLE. A model that can make an allowed server echo its guesses learns the key one character per response from
which guesses come back masked; Hub recovered a 48-character synthetic key in 40 responses. "Withhold the whole
answer on a partial match" is still a one-bit oracle. So: whole forms only, plus three bounds on what's left:
- a value shorter than 16 characters is refused (`key_unusable`): whole-form masking can't protect it;
- a per-item call budget, 30 calls per hour per item per agent (`too_many_calls`), bounds any remaining guessing;
- a server that echoes a short fragment (a "last 4" display) is accepted leakage: it shows that much anyway.
Tests run against an echo server that reflects the Authorization header and body in every encoding, for two values
(one with a quote, backslash and non-ASCII character, so its JSON, percent and hex forms all differ).

**G4 Writes ask the owner (principle #10).** DELETE always asks first. POST/PUT/PATCH ask first by default. The
ask is the RUNTIME's own confirm for the exact call it is about to make (not a Vault card; Vault 2026-10-27:
compatible with KEEP 5 under these conditions): (a) code writes every word in one fixed sentence, e.g. "Your agent
wants to send a DELETE to api.github.com with this path:"; the method comes from the fixed set, and the host comes
from the PARSED URL and must already be in `lease.targets`; (b) the path is shown as DATA: monospace, escaped,
labelled "path (written by your agent)", never part of the sentence, cut with a visible "..." when long and shown in
full on tap; (c) bound to the hash of the exact request: single use, short expiry, so a different request needs a
new yes; (d) no body text in v1, only "a body of N bytes". Its words go to Grant first, and Hub reads the design. TAINT is sticky:
once untrusted content (a web fetch, an email body, a non-owner message) is in the context, every write goes to a
card until the session is cleared. "Allow writes to this host without asking" is a LATER owner opt-in; its words go
to Grant via Boss, and it's never a default (same rule as auto-save). GET with a bound host runs without a card.

**G5 Limits (as built in windy-agent #655).** One lease per call. URL ≤ 2 KiB; request body ≤ 64 KiB. The DECODED
response is streamed and refused over 1 MiB (`call_failed`; a gzip bomb stops there); the model gets at most 64 KiB
of it (`truncated`). 20 s per network step and a 30 s overall deadline. Only gzip/deflate are accepted; no redirects
are followed; proxy environment variables are ignored; certificates are always verified. Dot-segments (`.`, `..`,
`%2e`) are refused so the path sent is the path shown. No value in any exception message. Codes and words: windy-
contracts `schema/agent/vault-tools.v1.json` (merged 0c37d1c8), shared with the roster.

## Dependencies (owners)

- **Windy Vault:** V7.6 c2e: `Item.targets` (owner route, fresh sign-in), `lease.targets` (agent-routes minor) and
  the shared FillTarget type. Plan codon + contract draft to Hub this week; it ships dark in the next sandbox release.
  The redactor (c2b, DEFERRED by Boss 10-07, #457) comes back with this tool: stated in the call_api GO ask so the
  plan is aligned, with Vault updating c2b's text.
- **Windy Hub + Mobile + desktop:** the write-confirm screen on phone and desktop (G4 a-d; words to Grant first).
- **Windy Agent:** the tool, G2/G3/G4 in the Windy Fly harness, and the echo-server tests. Coded against Vault's
  contract once Vault fixes it.
