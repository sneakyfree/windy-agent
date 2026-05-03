# Security audit — 2026-05-02

Adversarial review of the Capability Plane gate + shell blocklist.
Conducted as part of the autonomous hardening sprint after the v13/
v14 Q&A campaign (PRs #117-#125).

## Scope

The architecture doc claims:

> *"Security is the choke point everything routes through... passport-
> band capability gating, redacted secrets"*

This audit verified that claim by:

1. Adversarially testing the capability dispatch path
   (`registry.invoke` / `invoke_sync`)
2. Probing the shell blocklist for bypass patterns
3. Pinning the gate behavior with a regression suite

Out of scope:
- Docker isolation (the documented "real defense" — has its own test suite)
- LLM behavior under prompt injection (covered by v14 battery)
- Network egress controls
- Secret-redaction in logs

## Findings

### Capability gate (`registry.invoke`)

**Status: structurally sound. 26/26 adversarial tests pass.**

What I verified:

- Unknown capability IDs raise `KeyError` — no silent fallback
- Capability IDs with newlines / null bytes / semicolons / unicode
  homographs (Cyrillic 'а' for Latin 'a') raise `KeyError`
- Band gate fires BEFORE handler invocation in all 16 (session,
  required) pairs across the Band enum — handler never runs for
  denied calls (verified with side-effect-recording handlers)
- Runtime tier escalation re-checks band when args trigger it
  (e.g., `fs.write_file` with `overwrite=True`)
- Pre-invoke hook crashes do NOT bypass the gate — `CapabilityDenied`
  still raises
- Pre-invoke hooks see only allowed calls (denied calls don't fire
  pre-hooks — prevents leak of denied-attempt info to billing/audit
  hooks that aren't trust-aware)
- Args containing dunder names (`__class__`, `__globals__`, `__bases__`)
  pass to handler unchanged — registry doesn't interpret them
  (handler is responsible for validation; registry's job is to deliver
  unmodified)
- 1MB args don't crash dispatch
- 20 concurrent invocations of the same capability don't bleed state
  through the registry layer (handler is responsible for its own
  thread-safety; the registry is)
- Post-invoke hooks fire even when handler raises (audit row gets
  marked failed instead of staying pending)

**Pinned by:** `tests/test_capability_security_audit.py` (26 tests)

### Shell blocklist — 5 holes found, all closed

The pre-audit blocklist had real bypasses. Tightened in this PR:

| # | Bypass | Pre-audit | Post-audit |
|---|--------|-----------|------------|
| 1 | `rm -fr /` (flag-order swap, `f` before `r`) | ❌ allowed | ✅ blocked |
| 2 | `rm -RF /` / `rm -Rf /` (uppercase) | ❌ allowed | ✅ blocked |
| 3 | `bash -c 'rm -rf /'` (single-quoted) | ❌ allowed | ✅ blocked |
| 4 | `bash -c "rm -rf /"` (double-quoted) | ❌ allowed | ✅ blocked |
| 5 | `dd if=x of="/dev/sda"` (quoted device) + uppercase `DD` | ❌ allowed | ✅ blocked |

**Root cause:**

- The `rm -rf /` regex required `r` before `f` in the flag string
  (`[a-zA-Z]*r[a-zA-Z]*f`). Now uses two zero-width lookaheads to
  require both letters present in any order.
- The trailing terminator `(\s|$|\*)` only accepted whitespace, end-
  of-string, or `*`. Now also accepts quote chars, so `rm -rf /'`
  inside a quoted command-substitution still matches.
- The `dd` regex required `of=/dev/...` exactly. Now accepts
  optional quote: `of=['"]?/dev/...`.
- All patterns now `re.IGNORECASE`.

**Pinned by:** `tests/test_shell_blocklist_security.py` (44 tests)

### Bypasses we accept (Docker is the real defense)

The blocklist is *belt-and-suspenders*. The Wave 5 doc explicitly
says Docker isolation is the primary defense. Documented bypasses
that the regex layer doesn't catch (and shouldn't, without unbounded
complexity):

- **Base64 / hex encoding:** `echo cm0gLXJmIC8K | base64 -d | bash`
  — regex sees the encoded bytes, not the decoded `rm -rf /`.
- **Variable indirection:** `X=rm; $X -rf /` — regex sees `$X`.
- **Alphabet substitution:** `r''m -rf /` — regex sees `r''m`,
  which isn't a word boundary match for `rm`.

If Docker isn't available and a hostile shell hits the host_rw
path, these bypasses succeed. **Mitigation today:** host_rw
requires Band.OWNER (verified by tests). **Mitigation Wave 5:**
Docker becomes mandatory for non-OWNER bands.

## What's NOT covered yet

These are the next adversarial targets I'd recommend, NOT addressed
in this audit:

1. **Docker dispatcher invariants** — what happens when Docker is
   present but unhealthy? When the bind-mount allowed_roots list
   contains a path traversal? When the image is missing?
2. **`fs.write_file` overwrite=true escalation** — pinned by
   capability tests above, but not adversarially fuzzed against
   path traversals (`../../../../etc/shadow`, symlink races).
3. **Email send / Cloudflare / GitHub external-effect capabilities**
   — band-gated, but no test for "what if the LLM's tool args ask
   us to send to a phishing target?"
4. **MCP / ACP exposure (Wave 8)** — once external tools can
   register capabilities, the descriptor itself becomes attacker-
   controlled input. None of today's tests cover that surface.
5. **Audit log integrity** — the post-invoke hooks fire, but is
   the audit row tamperproof? (Today: no — it's a SQLite row.)

## Sign-off

Adversarial pass shipped as PR #126. **22 net-new regression tests
pin the gate; 5 real bypasses closed; 0 behavior changes for
legitimate commands.**

The doc claim "passport-band capability gating" is now backed by
tests, not just documentation.
