"""Why Windy Mind answered with a different model than the one picked, in Mind's own words (Build-131 note 1).

Grant picked Haiku, Mind served another model, and the agent INVENTED a reason ("the setting only tweaks
phrasing"). The prompt said which model answered but not what was picked or why, so the model guessed. Mind now
says it (windy-contracts menu.v1 1.13.0, windy-mind #490): on a reply served by a different model than the pick,
the body carries ``windy_served`` {requested_model, served_model, requested_name, served_name, reason, line_added}
(null when served as picked), with headers ``x-mind-served-reason`` / ``x-mind-requested-model`` /
``x-mind-served-line``.

This module renders Mind's ONE line from Mind's template and Mind's fixed words, never words of our own, for the
agent's RUNTIME CONTEXT. Display names only; a missing name reads as Mind's defaults; an unknown reason code reads
as "unknown" and the raw code is never shown. When Mind already put the line into that request's prompt
(``line_added``), it is not repeated. Only the MOST RECENT reply counts: a reply served as picked clears it.
"""

from __future__ import annotations

from typing import Any, Mapping

# menu.v1 1.13.0 (windy-contracts 21ca16da): x-served-reason-words and x-served-reason-line, verbatim.
WORDS = {
    "surface_pinned": "this kind of conversation uses a fixed model",
    "pick_blocked_by_switch": "the owner's 'Use outside AI companies' setting is off",
    "outside_ai_not_allowed": "the owner's 'Use outside AI companies' setting is off",
    "unservable": "the picked model isn't available on this account",
    "blocked": "the picked model isn't available right now",
    "quota_exhausted": "the picked model has reached its usage limit for now",
    "budget": "the spending limit for this agent has been reached",
    "unknown": "the picked model couldn't be used for this message",
}
LINE = ("Windy Mind: you are answering as {served_name}, not {requested_name}, because {words}. If anyone asks "
        "which model you are, say so plainly. Don't give any other reason.")
DEFAULT_REQUESTED = "the model you picked"
DEFAULT_SERVED = "a Windy model"
_NAME_MAX = 60


def _name(value: Any, default: str) -> str:
    text = " ".join(str(value).split()) if isinstance(value, str) else ""
    text = "".join(ch for ch in text if ch.isprintable())[:_NAME_MAX].strip()
    return text or default


def parse(body: Any, headers: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """Mind's answer about the model that served -> {"line": str | None, "added": bool}, or None when Mind said
    nothing either way (an older Mind, or not Mind at all). {"line": None} means: served as picked."""
    served = body.get("windy_served", ...) if isinstance(body, dict) else ...
    reason_header = (headers or {}).get("x-mind-served-reason") if headers is not None else None
    if served is ... and not reason_header:
        return None                                        # Mind said nothing: older Mind or another provider
    if served is None and not reason_header:
        return {"line": None, "added": False}               # served as picked
    info = served if isinstance(served, dict) else {}
    reason = str(info.get("reason") or reason_header or "unknown").strip()
    words = WORDS.get(reason, WORDS["unknown"])             # never the raw code
    added = info.get("line_added") is True or str((headers or {}).get("x-mind-served-line") or "") == "added"
    line = LINE.format(served_name=_name(info.get("served_name"), DEFAULT_SERVED),
                       requested_name=_name(info.get("requested_name"), DEFAULT_REQUESTED), words=words)
    return {"line": line, "added": added}


# The most recent reply's line per session (what the next turn's RUNTIME CONTEXT shows).
_LAST: dict[str, str] = {}


def note(session_id: str, served: dict[str, Any] | None) -> None:
    """After each reply. Only a reply Mind marked as replaced (and whose prompt didn't already carry the line)
    leaves a line; anything else clears it, so it never carries over once the pick is serving again."""
    if served and served.get("line") and not served.get("added"):
        _LAST[session_id] = str(served["line"])
    else:
        _LAST.pop(session_id, None)


def line_for_prompt(session_id: str) -> str | None:
    return _LAST.get(session_id)


RULE = ("- Why a different model answered: only a 'Windy Mind:' line in this context says why. Without one, say "
        "you don't know why; never give a reason of your own.")


def reset_for_tests() -> None:
    _LAST.clear()
