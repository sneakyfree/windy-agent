"""Model catalog — single source of truth for model metadata.

Used by:
  - ``/model`` command (display, alias resolution, set)
  - ``/memory`` command (cap-vs-model conflict resolution)
  - ``/status`` rendering (display labels)
  - ``_call_anthropic`` (auto-attach 1M beta header when cap > native)

Adding a new model means adding one entry here. No other file needs
to change to surface it in the ``/model`` listing.

Surfaced 2026-05-19 (PR #197) when Grant asked for user-controllable
model + memory settings: "we want the user to be able to decide what
the context refresh window is or have it be the default, ... and
which model itself."
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelInfo:
    """Metadata for a single model the user can select."""

    id: str
    """Canonical model id passed to the provider SDK (e.g.
    ``claude-opus-4-7``)."""

    family: str
    """``opus`` / ``sonnet`` / ``haiku`` / ``gpt`` / ``llama`` — used
    for emoji + grouping in ``/model`` output."""

    native_cap: int
    """Default context window in tokens (200K = 200_000)."""

    extended_cap: int | None = None
    """Optional larger cap available via a beta header. None means
    no extended-context tier."""

    extended_beta_header: str | None = None
    """The ``anthropic-beta`` value that unlocks ``extended_cap``.
    Today only one model family uses this — Anthropic's
    ``context-1m-2025-08-07``."""

    aliases: tuple[str, ...] = field(default_factory=tuple)
    """User-friendly names that resolve to this model id."""

    description: str = ""
    """One-line plain-English summary for ``/model`` output. Grandma-
    readable."""


# Order matters for ``/model`` listing — smartest → balanced → fastest
# reads naturally to a non-technical user.
_MODELS: list[ModelInfo] = [
    ModelInfo(
        # Bumped 4-7 → 4-8 (the current default, e.g. Windy 0). Without a
        # 4-8 entry /context and /memory fell back to an 8000-token default
        # for the very model the agent runs (2026-07-06).
        id="claude-opus-4-8",
        family="opus",
        native_cap=1_000_000,
        aliases=("opus", "smartest", "claude-opus", "claude-opus-4-7"),
        description="Most capable, slower",
    ),
    ModelInfo(
        id="claude-sonnet-4-6",
        family="sonnet",
        native_cap=200_000,
        extended_cap=1_000_000,
        extended_beta_header="context-1m-2025-08-07",
        aliases=("sonnet", "balanced", "claude-sonnet"),
        description="Balanced — fast and smart",
    ),
    ModelInfo(
        id="claude-haiku-4-5",
        family="haiku",
        native_cap=200_000,
        extended_cap=1_000_000,
        extended_beta_header="context-1m-2025-08-07",
        aliases=("haiku", "fastest", "claude-haiku"),
        description="Fastest & cheapest",
    ),
]


def list_models() -> list[ModelInfo]:
    """Return the full catalog in display order."""
    return list(_MODELS)


def resolve(name_or_alias: str) -> ModelInfo | None:
    """Return the ``ModelInfo`` matching ``name_or_alias``, or None.

    Match precedence:
      1. Exact id match (``claude-opus-4-7``)
      2. Alias match (``opus``, ``smartest``, ``claude-opus``)
      3. Substring on aliases — e.g., ``opus-4`` -> opus
      4. Family prefix on canonical id — ``claude-opus-4-7-20251001``
         still matches claude-opus-4-7

    Case-insensitive. Used by ``/model <name>`` and to map env
    ``DEFAULT_MODEL`` to a catalog entry.
    """
    if not name_or_alias:
        return None
    needle = name_or_alias.strip().lower()
    for m in _MODELS:
        if needle == m.id.lower():
            return m
    for m in _MODELS:
        for a in m.aliases:
            if needle == a.lower():
                return m
    # Lenient match — catches dated variants like
    # claude-sonnet-4-6-20251022 → claude-sonnet-4-6.
    for m in _MODELS:
        if needle.startswith(m.id.lower()):
            return m
    return None


def is_known(model_id: str) -> bool:
    """True if ``model_id`` is in the catalog (exact or dated variant)."""
    return resolve(model_id) is not None


def supports_cap(model_id: str, cap: int) -> tuple[bool, str | None]:
    """Can this model serve a context window of ``cap`` tokens?

    Returns ``(ok, beta_header)``:
      - ``(True, None)`` — cap is within the model's native limit;
        no beta header needed.
      - ``(True, "context-1m-2025-08-07")`` — cap is within the
        model's extended limit; caller must add this beta header.
      - ``(False, None)`` — cap exceeds what this model supports.
        Caller should refuse and suggest a different model.
    """
    m = resolve(model_id)
    if m is None:
        # Unknown model — defer to a conservative 200K to avoid
        # confidently approving caps we can't validate.
        return (cap <= 200_000, None)
    if cap <= m.native_cap:
        return (True, None)
    if m.extended_cap is not None and cap <= m.extended_cap:
        return (True, m.extended_beta_header)
    return (False, None)


def format_cap(cap: int) -> str:
    """Render a token count as ``200K`` / ``1M`` for display."""
    if cap >= 1_000_000:
        return f"{cap // 1_000_000}M"
    if cap >= 1_000:
        return f"{cap // 1_000}K"
    return str(cap)


def parse_cap(text: str) -> int | None:
    """Parse a user-supplied cap like ``200K`` / ``1M`` / ``500000``
    into an integer token count. Returns None on garbage input.

    Accepts: ``1M``, ``1m``, ``1_000_000``, ``500K``, ``500k``,
    ``200000``. Whitespace + commas stripped. Negative / zero values
    return None (caller should treat as invalid).
    """
    if not text:
        return None
    s = text.strip().lower().replace(",", "").replace("_", "")
    multiplier = 1
    if s.endswith("m"):
        multiplier = 1_000_000
        s = s[:-1]
    elif s.endswith("k"):
        multiplier = 1_000
        s = s[:-1]
    s = s.strip()
    try:
        n = float(s)
    except ValueError:
        return None
    n = int(n * multiplier)
    if n <= 0:
        return None
    return n


def family_emoji(family: str) -> str:
    """Emoji for a model family — used in ``/model`` listing."""
    return {
        "opus": "🧠",
        "sonnet": "⚖️",
        "haiku": "⚡",
        "gpt": "🤖",
        "llama": "🦙",
    }.get(family, "🤖")
