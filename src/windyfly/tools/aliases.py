"""One-release aliases for tool names retired by the tool trim (phase 1, 10-21).

A model, a saved skill or an old transcript may still call a name that was merged into another tool. The
dispatcher (agent/loop.py, the MCP server) resolves it here to the new name plus fixed arguments, so the call
still works. Aliases are never offered to the model: no schema carries them, and they do not count toward an
owner turn's tools. Remove this table one release after the trim.
"""

from __future__ import annotations

import json
import logging
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)


class Alias(NamedTuple):
    target: str                                # the tool that does this job now
    fixed: dict[str, Any] = {}                 # arguments set by the alias (they win over the caller's)
    rename: dict[str, str] = {}                # old argument name -> new argument name


TOOL_ALIASES: dict[str, Alias] = {
    # Legacy (ToolRegistry) tools.
    "windycodeweb_start_from_template": Alias("windycodeweb_start", rename={"slug": "template"}),
    "windycodeweb_create_project": Alias("windycodeweb_start", fixed={"blank": True}),
    # A builder call that answers 'unavailable' when the builder is not there, else the projects.
    "windycodeweb_status": Alias("windycodeweb_list_projects"),
    # The IDE tools each answer 'unavailable' when Windy Code is closed; this lists the projects.
    "windycode_status": Alias("windycode_list_projects"),
    # Capabilities.
    "vision.ocr": Alias("vision.describe", fixed={"read_text": True}),
    "mcp.list_servers": Alias("mcp.list_tools"),
    "mind.list_models": Alias("mind.status"),
    "cloudflare.zone_details": Alias("cloudflare.list_zones"),
    "setup.start": Alias("setup.status"),
}


def canonical(name: str) -> str:
    """The name that does the job now (``name`` itself when it is not an alias)."""
    alias = TOOL_ALIASES.get(name)
    return alias.target if alias else name


def resolve(name: str, args: Any) -> tuple[str, Any]:
    """(new name, new arguments) for an aliased call; anything else comes back unchanged.

    ``args`` may be a dict or the model's JSON string. Malformed JSON is passed through as-is so the
    dispatcher reports it the same way it does for any tool.
    """
    alias = TOOL_ALIASES.get(name)
    if alias is None:
        return name, args
    parsed = args
    if isinstance(args, str):
        try:
            parsed = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            logger.info("tool alias: %s -> %s (arguments left as sent)", name, alias.target)
            return alias.target, args
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        return alias.target, args
    out = {alias.rename.get(k, k): v for k, v in parsed.items()}
    out.update(alias.fixed)
    logger.info("tool alias: %s -> %s", name, alias.target)
    return alias.target, out
