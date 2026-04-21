# Writing a Capability — Author's Guide

This is the recipe for adding a new capability to Windy Fly. After
Wave 2 the Capability Plane is the canonical way new tools get added —
every "hand" plugs into the same band-gated, audited slot. Adding a new
capability is filling out a form, not designing infrastructure.

The worked example is `fs.read_file` from PR #56 — a real shipped
capability you can read alongside this guide
(`src/windyfly/agent/capabilities/filesystem.py`).

---

## TL;DR — the recipe

1. Pick a **tier** (0–5) based on what your capability does.
2. Write the **handler** function (sync or async).
3. Build a **`Capability` descriptor** with the handler, JSON schema, and
   any policy overrides on top of the tier defaults.
4. Register it in a `register_*_capabilities(registry, config)` function.
5. Hook the registration into `main.py` after `install_audit_hooks(...)`.
6. Write tests mirroring `tests/test_capability_filesystem.py`.

That's it. No schema migration, no audit wiring, no LLM tool-list
plumbing — Wave 2 already gave you all of that for free.

---

## Step 1 — Pick a tier

The tier is the LLM-friendly summary of how risky / privileged your
capability is. From `src/windyfly/agent/capabilities/descriptor.py`:

| Tier | Examples | Default band | Default sandbox | Audit |
|---|---|---|---|---|
| 0 — `PURE_COMPUTE` | dice, calc, translate | SANDBOX | none | off |
| 1 — `READ_EXTERNAL` | web search, read_file, list_dir | USER | host_readonly | on |
| 2 — `WRITE_LOCAL_SAFE` | write_file (new), draft_email | USER | host_rw | on, dry-run available |
| 3 — `WRITE_DESTRUCTIVE` | delete, move, git commit | TRUSTED | host_rw | on, undo mandatory |
| 4 — `EXTERNAL_EFFECT` | send email, post msg, git push | TRUSTED | host_rw | on |
| 5 — `FULL_MACHINE` | shell exec, install pkg | TRUSTED | docker | on |

Picking a tier sets sensible defaults for everything else. **You can
override any default field** on the `Capability` descriptor — the tier
is a starting point, not a constraint. `fs.read_file` uses the
`READ_EXTERNAL` defaults exactly; nothing overridden.

---

## Step 2 — Write the handler

The handler is a Python function (sync or async) that does the actual
work. Args are passed as keyword arguments, so use a keyword-only
signature with `*`:

```python
def my_capability(*, path: str, max_bytes: int = 100_000) -> dict:
    # Do the work. Raise on errors — the dispatcher catches and routes
    # to the typed-error classifier from #50.
    return {"path": path, "result": "..."}
```

**Return type:** anything JSON-serializable. The dispatcher
(`agent/loop.py:_dispatch_tool_call`) converts non-string returns to
JSON automatically before handing the result to the LLM. Returning a
dict is the cleanest pattern — the LLM can navigate it.

**Errors:** raise standard Python exceptions. The dispatcher routes
them as JSON error envelopes back to the LLM. `PermissionError` is the
conventional one for "the user/LLM tried something they're not allowed
to do" — the typed-error classifier maps it to `capability_denied`.

**Async:** if your handler does I/O, make it `async def`. The registry's
`invoke_sync` adapter handles the bridging from sync agent code to
async handlers automatically (Wave 2 #4 / PR #55).

---

## Step 3 — Build the descriptor

```python
from windyfly.agent.capabilities import Capability, Tier

cap = Capability(
    id="myns.my_capability",
    description=(
        "One-line description of what this does. The LLM reads this to "
        "decide when to call. Be specific about preconditions and what "
        "the return value looks like."
    ),
    handler=my_capability,
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or ~-expanded path.",
            },
            "max_bytes": {
                "type": "integer",
                "description": "Cap on bytes returned (default 100000).",
            },
        },
        "required": ["path"],
    },
    tier=Tier.READ_EXTERNAL,
    scope="filesystem_allowlist",   # free-form for now; formalized later
)
```

**Naming:** use `namespace.action` format. Existing namespaces: `fs.`
(filesystem), planned: `git.` `email.` `shell.` `agent.` `web.` `gh.`.
Pick a namespace that groups related capabilities; the LLM uses the
namespace prefix as a hint.

**Description:** this is what the LLM sees in the tool list. Bad
descriptions cause bad tool calls. Be specific about:
- What the capability does (the verb)
- What the inputs are (arg shape)
- What the return value looks like (so the LLM knows what to do with
  it)
- Any preconditions ("path must be inside the agent's allowed roots")

**input_schema:** standard JSON Schema. The LLM uses this to construct
valid tool calls. `required` is honored; missing required fields cause
the LLM to either retry with fixes or give up.

**Overriding tier defaults:** if your capability needs different policy
than the tier suggests, set the field explicitly. Example —
audit-required pure compute:

```python
Capability(
    id="dice.audited_roll",
    description="Roll a die with audit trail (compliance requirement).",
    handler=lambda sides=6: 4,
    tier=Tier.PURE_COMPUTE,        # tier default: audit_required=False
    audit_required=True,            # override to True
)
```

---

## Step 4 — Register it

Group related capabilities into a `register_*_capabilities()` function
in their own module:

```python
# src/windyfly/agent/capabilities/myns.py

from windyfly.agent.capabilities import Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry


def register_myns_capabilities(
    registry: CapabilityRegistry,
    config: dict | None = None,
) -> None:
    """Register all capabilities in the myns namespace."""
    cfg = (config or {}).get("capabilities", {}).get("myns", {})

    # Pull any per-namespace config (allowlists, defaults) from the toml
    some_setting = cfg.get("some_setting", "default_value")

    def my_handler(*, path: str) -> dict:
        # ... use some_setting from closure
        return {"ok": True}

    registry.register(Capability(
        id="myns.my_action",
        description="...",
        handler=my_handler,
        input_schema={...},
        tier=Tier.READ_EXTERNAL,
    ))
```

The closure pattern lets you pull config (allowlist roots, API keys,
defaults) from `windyfly.toml` at registration time and bake them into
the handler — see how `register_filesystem_capabilities` does this with
`allowed_roots` in `filesystem.py`.

---

## Step 5 — Hook into main.py

After `install_audit_hooks(capability_registry, db, write_queue)` in
`main.py`'s channel branch, add:

```python
from windyfly.agent.capabilities.myns import register_myns_capabilities
register_myns_capabilities(capability_registry, config)
```

Order matters: capability registration must happen *after*
`install_audit_hooks` so the audit hook is in place before any
invocation.

**Per-channel registration:** today the channel branches in `main.py`
register capabilities individually. Once Wave 2 #5 (existing-tool
migration) lands, expect a single `register_all_capabilities(...)`
helper that's called from one place — but the per-namespace
`register_*_capabilities` functions stay as the unit.

---

## Step 6 — Write tests

The test pattern lives in `tests/test_capability_filesystem.py`. Mirror
it. Three layers of test:

**1. Pure handler tests** (no registry) — test the handler function
directly with various args, verifying happy path, edge cases, and
errors:

```python
def test_my_handler_returns_expected_shape():
    out = my_handler(path="/tmp/test")
    assert out["ok"] is True

def test_my_handler_raises_on_invalid_path():
    with pytest.raises(PermissionError):
        my_handler(path="/etc/passwd")
```

**2. Registry integration tests** — register the capability into a
fresh `CapabilityRegistry`, invoke through the registry, verify
band-gating works:

```python
@pytest.mark.asyncio
async def test_my_cap_through_registry():
    r = CapabilityRegistry()
    register_myns_capabilities(r, config={...})
    out = await r.invoke("myns.my_action", {"path": "/tmp/x"}, Band.USER)
    assert out["ok"]

@pytest.mark.asyncio
async def test_sandbox_band_denied():
    r = CapabilityRegistry()
    register_myns_capabilities(r, config={...})
    with pytest.raises(CapabilityDenied):
        await r.invoke("myns.my_action", {"path": "/tmp/x"}, Band.SANDBOX)
```

**3. Audit-enabled tests** (only if your capability has interesting
audit behavior) — install hooks, invoke, assert rows landed:

```python
@pytest.mark.asyncio
async def test_my_cap_lands_in_audit_ledger(db_and_wq):
    db, wq = db_and_wq
    r = CapabilityRegistry()
    install_audit_hooks(r, db, wq)
    register_myns_capabilities(r, config={...})

    await r.invoke("myns.my_action", {"path": "/tmp/x"}, Band.OWNER)
    _drain(wq)

    rows = get_actions_for_capability(db, "myns.my_action")
    assert len(rows) == 1
    assert rows[0]["success"] == 1
```

The fixtures `db_and_wq` and helper `_drain` live in
`tests/test_agent_actions_audit.py`. Copy the patterns from there.

**Security tests are required** for Tier 1+ capabilities: prove the
allowlist (or whatever your safety contract is) actually blocks
out-of-bounds calls. See `test_rejects_symlink_pointing_outside_allowlist`
in the filesystem tests for the pattern.

---

## What you get for free

When you've done the six steps above, your capability automatically:

- **Appears in the LLM's tool list** for any session at or above
  `band_required` (via `tool_schemas_for_band(band)` in
  `agent/loop.py:201`).
- **Lands an `agent_actions` row** for every invocation (via the audit
  hooks installed in `main.py`). Args are JSON-redacted by the audit
  hook before storage so secrets don't leak.
- **Routes through `CapabilityDenied → typed-error classifier (#50)`**
  if a session's band is too low — the user gets a friendly message,
  not a stack trace.
- **Is dispatched correctly from sync agent code** via
  `invoke_sync` (Wave 2 #4 / PR #55), which handles the
  async-from-sync bridging without you thinking about it.
- **Shows up in `/pulse`** under the capability stats line (once Wave 1
  #49 chain lands and the capability count display is wired in).
- **Feeds the Wave 7 optimizer** via `agent_actions.outcome_score`
  (when Wave 7 ships, your existing capability is automatically
  scoreable — no changes needed).

---

## Common pitfalls

**Don't write your own retry logic.** The agent loop's tool re-loop
already retries failed tool calls (up to `tool_reloop_rounds` rounds,
slider-controlled). Adding per-capability retry compounds the round
count.

**Don't write your own audit code.** The audit hook in
`agent/capabilities/audit.py` writes rows. If you find yourself wanting
to log "this capability did X" — that's already in the audit ledger.
Use `get_actions_for_capability(db, cap_id)` to query.

**Don't hard-code paths or secrets in the handler.** Pull from `config`
in the `register_*_capabilities` function; pass via closure. This makes
per-instance customization (grandma's allowlist vs Grant's) trivial.

**Don't bypass the band check.** Even if your capability is "obviously
safe," declare its band correctly. The Capability Plane's value comes
from every capability honoring the same gate; one rogue exception
breaks the property.

**Don't put capability handlers in `tools/` (the legacy location).**
That's the old `ToolRegistry` path. New work goes in
`src/windyfly/agent/capabilities/<namespace>.py`. Wave 2 #5 will
migrate the legacy `tools/` modules into capabilities.

---

## Reference reading

- **`src/windyfly/agent/capabilities/descriptor.py`** — `Capability`
  dataclass, tier defaults, band hierarchy
- **`src/windyfly/agent/capabilities/registry.py`** — `CapabilityRegistry`,
  `invoke_sync`, hooks
- **`src/windyfly/agent/capabilities/audit.py`** — pre/post audit hooks,
  arg redaction
- **`src/windyfly/agent/capabilities/filesystem.py`** — the canonical
  worked example: `fs.read_file` and `fs.list_directory`
- **`tests/test_capability_filesystem.py`** — full test patterns
- **`docs/ARCHITECTURE.md`** (Wave 2 #N) — the 10-plane framing this
  Plane lives in
- **User memory: `project_windy_fly_architecture.md`** — strategic
  framing for *why* the Capability Plane exists at all
