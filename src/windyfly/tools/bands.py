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
    "search_domain", "suggest_domains", "windycodeweb_list_templates",
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
    "windycode_list_projects", "windycode_read_file", "windycodeweb_list_cabinet",
)
# Writes to the owner's own data (lists, calendar, private drafts).
_LOCAL_WRITE = (
    "add_todo", "complete_todo", "set_reminder", "cancel_reminder", "create_event",
    "windycodeweb_add_files", "windycodeweb_edit_text", "windycodeweb_start", "windycodeweb_undo",
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


# A turn another agent triggered with a typed task (agent-teamwork.v1) gets ONLY these tools: public reads,
# nothing that writes, sends or asks another agent (Hub on #602: TRUSTED alone still offered message_agent).
# agent/loop.py offers only these and refuses any other call on such a turn. Not fetch_url: a GET to a URL
# another agent chose can carry data out in its query string (Hub on #605).
TASK_TURN_TOOLS = frozenset(_PUBLIC_READ) - {"fetch_url"}
_task_turn: contextvars.ContextVar[frozenset[str] | None] = contextvars.ContextVar("windy_task_turn", default=None)


@contextmanager
def task_turn_tools(allowed: frozenset[str] = TASK_TURN_TOOLS) -> Iterator[None]:
    token = _task_turn.set(allowed)
    try:
        yield
    finally:
        _task_turn.reset(token)


def task_turn_allowlist() -> frozenset[str] | None:
    """The tools a task turn may use, or None on any other turn."""
    return _task_turn.get()


# ── Goal turns: a background goal runs at TRUSTED, and an owner-only step pauses it (Boss BOARD 2026-11-07T19:26:03Z) ──
#
# A goal's own turns (goal_autorun, goal_pacing) have no human in them, so they run at TRUSTED: host_rw, the soul's
# owner-only parts, the owner's memory and mail, model switching and anything that acts outward are refused. In a goal
# turn the owner-only tools stay LISTED, so the model can try one, but the gate refuses every such call: the refusal
# records the tool's NAME here, every later call in that turn is refused too, and the goal code pauses the goal and
# asks the owner in chat. Resuming only unpauses: the goal never gets an owner grant (Hub's conditions on design A).
#
# The record is a mutable holder made fresh by goal_turn() at the start of each goal turn: a capability that runs in
# its own asyncio task works on a COPY of the context, so a ContextVar.set() there would be lost, but the holder is
# the same object in every copy. Turns never share one.

PAUSED_TEXT = "paused, needs the owner"

_goal_holder: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar("windy_goal_turn", default=None)


@contextmanager
def goal_turn() -> Iterator[None]:
    """Mark the running turn as a background goal's own turn, with a fresh (empty) needs-owner record."""
    token = _goal_holder.set({})
    try:
        yield
    finally:
        _goal_holder.reset(token)


def in_goal_turn() -> bool:
    return _goal_holder.get() is not None


def note_needs_owner(tool_name: str) -> None:
    """In a goal turn, record the FIRST owner-only tool it tried (the name only, never its arguments)."""
    holder = _goal_holder.get()
    if holder is not None and "tool" not in holder:
        holder["tool"] = str(tool_name)[:80]


def needs_owner() -> str | None:
    """The owner-only tool this goal turn tried, or None (always None outside a goal turn)."""
    holder = _goal_holder.get()
    return None if holder is None else holder.get("tool")


def listing_band(band: Any) -> Any:
    """The band tools are LISTED at: OWNER in a goal turn (so a refusal can happen and pause it), else ``band``."""
    if band is not None and in_goal_turn():
        return Band.OWNER
    return band


# ── Commands: the band of the sender whose own command is running, set ONLY by the in-process gate ──
# channels/base.py resolves the sender's band and runs the command inside command_sender(band). A command that runs an
# agent turn (/benchmark) uses OWNER only when this says the owner's own command started it in this process; a band in
# the context dict is never trusted for that (Boss BOARD 2026-11-07T19:26:03Z).

_command_band: contextvars.ContextVar[Band | None] = contextvars.ContextVar("windy_command_band", default=None)
_docker_only: contextvars.ContextVar[bool] = contextvars.ContextVar("windy_docker_only", default=False)


@contextmanager
def command_sender(band: Any) -> Iterator[None]:
    token = _command_band.set(None if band is None else Band(int(band)))
    try:
        yield
    finally:
        _command_band.reset(token)


def command_sender_band() -> Band | None:
    """The band of the sender whose own command is running in this process, or None (not inside a command)."""
    return _command_band.get()


@contextmanager
def docker_only() -> Iterator[None]:
    """The shell may use only its Docker sandbox in this block: host_rw is refused even at OWNER (/benchmark)."""
    token = _docker_only.set(True)
    try:
        yield
    finally:
        _docker_only.reset(token)


def docker_only_active() -> bool:
    return _docker_only.get()
