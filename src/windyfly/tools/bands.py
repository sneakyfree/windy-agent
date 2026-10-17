"""Which band may use each legacy (ToolRegistry) tool (Hub ruling 10-17: fail closed now, loosen later).

Capabilities carry ``band_required``; the older ToolRegistry tools carried nothing, so a USER-band turn
(guest mode, a text message from the owner's phone) and a TRUSTED sibling were offered every one of them,
and ``ToolRegistry.execute`` ran any tool the model named at any band, strangers included.

Now:
- Every legacy tool has a band here. A tool **not listed is OWNER-only** (deny by default), so a new tool
  cannot reopen the hole.
- USER keeps only tools that read public information. The owner's private data (calendar, inbox, to-dos,
  reminders, cloud files, mailbox, site projects), local writes and anything that acts outward (sends,
  calls, buys, publishes, uploads) are OWNER.
- The band is the **running turn's**: ``agent_respond`` sets it in a context variable for the turn, and
  ``ToolRegistry.execute`` reads it. It is not a parameter a caller can forget. With no turn band set, only
  USER tools run.

Loosening any of this (for example what an allowlisted family member may do) is Boss's and Grant's call.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Iterator

from windyfly.agent.capabilities.descriptor import Band

# Public information only: nothing about the owner, nothing written anywhere.
_PUBLIC_READ = (
    "fetch_url", "get_news", "get_weather", "web_search", "windy_plans",
    "search_domain", "suggest_domains", "windycodeweb_list_templates", "windycodeweb_status",
    "calculate", "convert_units", "flip_coin", "roll_dice", "list_languages",
)
# The owner's private data, read only (translate_text runs on the owner's account).
_PRIVATE_READ = (
    "get_today_events", "get_upcoming_events", "list_inbox", "list_reminders", "list_todos",
    "list_cloud_files", "list_my_domains", "my_mailbox", "windycodeweb_list_projects",
    "windycodeweb_list_checkpoints", "windycodeweb_list_editables", "windycodeweb_project_status",
    "windycodeweb_preview",
    "get_clone_status", "get_recordings", "get_translation_history", "translate_text",
    "list_my_sites", "list_site_versions", "site_status",
    "windycode_list_projects", "windycode_read_file", "windycode_status", "windycodeweb_list_cabinet",
)
# Writes to the owner's own data (lists, calendar, private drafts).
_LOCAL_WRITE = (
    "add_todo", "complete_todo", "set_reminder", "cancel_reminder", "create_event",
    "windycodeweb_create_project", "windycodeweb_add_files", "windycodeweb_edit_text",
    "windycodeweb_start", "windycodeweb_start_from_template", "windycodeweb_undo",
    "set_timer", "windycode_create_project", "windycode_write_file", "download_cloud_file",
    "windycodeweb_file_project", "windycodeweb_log_activity",
)
# Acts outward: sends, calls, buys, publishes, uploads.
_OUTWARD = (
    "send_email", "send_sms", "confirm_sms", "send_chat_message", "make_call", "upload_to_cloud",
    "buy_domain", "confirm_purchase", "set_auto_renew",
    "windycodeweb_publish", "windycodeweb_unpublish", "windycodeweb_connect_domain",
    "create_site", "add_or_edit_files", "publish_site", "undo_to_version", "connect_domain_to_site",
    "windycode_run", "windycode_save_to_git",
)

TOOL_CLASS: dict[str, str] = {
    **{n: "public read" for n in _PUBLIC_READ},
    **{n: "private read" for n in _PRIVATE_READ},
    **{n: "local write" for n in _LOCAL_WRITE},
    **{n: "outward" for n in _OUTWARD},
}
TOOL_BANDS: dict[str, Band] = {n: (Band.USER if c == "public read" else Band.OWNER) for n, c in TOOL_CLASS.items()}

_turn_band: contextvars.ContextVar[Band | None] = contextvars.ContextVar("windy_turn_band", default=None)


def required_band(name: str) -> Band:
    """The least band that may use ``name``. Not listed: OWNER (deny by default)."""
    return TOOL_BANDS.get(name, Band.OWNER)


def current_band() -> Band | None:
    return _turn_band.get()


def allowed(name: str, band: Any = None) -> bool:
    """May a turn at ``band`` (default: the running turn's) use ``name``? No band known: USER tools only."""
    b = current_band() if band is None else band
    need = required_band(name)
    if b is None:
        return need <= Band.USER
    return int(b) >= int(need)


@contextmanager
def turn_band(band: Any) -> Iterator[None]:
    """Set the band for the running turn. A nested call may only LOWER it (a tainted turn's cap)."""
    outer = _turn_band.get()
    b = Band(int(band))
    if outer is not None and b > outer:
        b = outer
    token = _turn_band.set(b)
    try:
        yield
    finally:
        _turn_band.reset(token)


@contextmanager
def cap_turn_band(band: Any) -> Iterator[None]:
    """Lower the running turn's band for one call (a tainted turn's cap). It never sets a band where the
    turn has none, so a tool call outside agent_respond stays deny-by-default."""
    outer = _turn_band.get()
    if outer is None:
        yield
        return
    with turn_band(min(Band(int(band)), outer)):
        yield


# The running turn's own words and a scratch dict for per-turn limits (the cloud upload cap), set by
# agent_respond next to the band. Tools read them; nothing outside a turn has them.
_turn_text: contextvars.ContextVar[str | None] = contextvars.ContextVar("windy_turn_text", default=None)
_turn_state: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("windy_turn_state", default=None)


@contextmanager
def turn_context(text: str) -> Iterator[None]:
    t1 = _turn_text.set(text)
    t2 = _turn_state.set({})
    try:
        yield
    finally:
        _turn_state.reset(t2)
        _turn_text.reset(t1)


def turn_text() -> str | None:
    return _turn_text.get()


def turn_state() -> dict[str, Any] | None:
    return _turn_state.get()


def mark_turn_tainted() -> None:
    """A tool says its result carries text a third party chose: the rest of this turn is tainted (the I2
    taint model in agent/loop.py refuses outward tools from then on). Outside a turn: nothing to mark."""
    state = _turn_state.get()
    if state is not None:
        state["tainted"] = True


def turn_marked_tainted() -> bool:
    state = _turn_state.get()
    return bool(state and state.get("tainted"))
