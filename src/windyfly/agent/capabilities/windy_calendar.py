"""windy_calendar.* : the owner's Windy Calendar, for windyfly agents (Agentic Calendar K1).

Distinct from the Google-Calendar tools (get_today_events, create_event). Descriptions are Calendar's own
factual one-liners (GET /tools, #9). TRUSTED band = the owner and the owner's own agents (siblings); a
stranger agent has none of them. Calendar enforces its own rules (a per-agent daily write ceiling the owner can lower, owner tap for cancel).
Dark: needs WINDY_CALENDAR=1, and the tools register only when Calendar answers for this agent at boot
(a calendar exists and the agent can see it), so an owner without Windy Calendar spends no tool budget.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from windyfly.agent import windy_calendar as wc
from windyfly.agent.capabilities.descriptor import Band, Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry

logger = logging.getLogger(__name__)

MAX_SLOTS = 50
DEFAULT_COUNT = 20


def _link_from(result: Any) -> str | None:
    if isinstance(result, str):
        return result or None
    if isinstance(result, dict):
        for key in ("url", "booking_url", "link", "booking_page"):
            v = result.get(key)
            if isinstance(v, str) and v:
                return v
    return None


def register_windy_calendar_capabilities(registry: CapabilityRegistry, config: dict[str, Any] | None = None) -> None:
    if not wc.enabled():
        return
    # Calendar must answer at boot (same rule as the other credentials): no calendar, not linked, a trust or
    # sign-in problem, or Calendar down = no calendar tools this run.
    probe = wc.invoke("get_booking_page")
    if not probe["ok"]:
        logger.info("windy calendar: tools not registered (Calendar did not answer for this agent)")
        return

    def availability(**kw: Any) -> dict[str, Any]:
        args: dict[str, Any] = {"from": kw.get("from_date"), "to": kw.get("to_date")}
        if kw.get("timezone"):
            args["timezone"] = kw["timezone"]
        n = kw.get("count")
        args["count"] = max(1, min(int(n), MAX_SLOTS)) if isinstance(n, int) else DEFAULT_COUNT
        out = wc.invoke("get_availability", args)
        if out["ok"] and isinstance(out.get("result"), dict):
            res = dict(out["result"])
            slots = res.get("slots") or []
            if len(slots) > MAX_SLOTS:
                res["slots"], res["truncated"] = slots[:MAX_SLOTS], True
            return {"ok": True, **res}
        return out

    def appointments(**kw: Any) -> dict[str, Any]:
        args = {k: kw[k] for k in ("from", "to", "timezone") if kw.get(k)}
        if kw.get("from_date"):
            args["from"] = kw["from_date"]
        if kw.get("to_date"):
            args["to"] = kw["to_date"]
        out = wc.invoke("list_appointments", args)
        return {"ok": True, "appointments": out["result"]} if out["ok"] else out

    def booking_link() -> dict[str, Any]:
        out = wc.invoke("get_booking_page")
        if not out["ok"]:
            return out
        link = _link_from(out.get("result"))
        if not link:
            return {"ok": False, "say": "My owner's booking link isn't ready yet."}
        res = out.get("result")
        listed = res.get("listed") if isinstance(res, dict) else None
        state = str(res.get("state") or "") if isinstance(res, dict) else ""
        if listed is False or state == "unlisted":
            return {"ok": True, "booking_link": link, "sharing_off": True,
                    "say": "My owner's booking link is turned off: anyone who opens it sees 'Not taking bookings'."}
        return {"ok": True, "booking_link": link}

    def book(**kw: Any) -> dict[str, Any]:
        args = {k: kw[k] for k in ("starts_at_utc", "booker_name", "booker_email", "booker_phone",
                                   "booker_note", "booker_tz") if kw.get(k)}
        args["idempotency_key"] = str(kw.get("idempotency_key") or uuid.uuid4())
        out = wc.invoke("book_appointment", args, write=True)
        return {"ok": True, **(out["result"] if isinstance(out.get("result"), dict) else {})} if out["ok"] else out

    def block(**kw: Any) -> dict[str, Any]:
        args = {k: kw[k] for k in ("starts_at_utc", "ends_at_utc", "reason") if kw.get(k)}
        out = wc.invoke("block_time", args, write=True)
        return {"ok": True, **(out["result"] if isinstance(out.get("result"), dict) else {})} if out["ok"] else out

    def create_meeting(**kw: Any) -> dict[str, Any]:
        args = {k: kw[k] for k in ("starts_at_utc", "duration_minutes", "title", "note", "location") if kw.get(k)}
        invitees = [{k: str(i[k]) for k in ("email", "name") if i.get(k)} for i in kw.get("invitees") or []]
        args["invitees"] = invitees
        out = wc.invoke("create_meeting", args, write=True)
        # Counts only in any log (Hub 10-10): never addresses, titles or locations.
        logger.info("windy calendar: create_meeting n_invitees=%d outcome=%s", len(invitees),
                    "pending_owner" if out.get("pending_owner") else ("ok" if out["ok"] else "refused"))
        if out.get("pending_owner"):
            return {"ok": False, "pending_owner": True, "confirmation_id": out.get("confirmation_id"),
                    "say": out.get("say")}
        return out

    date = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
    s = {"type": "string"}
    registry.register(Capability(
        id="windy_calendar.availability", name="Open times",
        description="Returns the open bookable slots for a date range in a timezone, earliest first; optional count limits to the first N. Slots are the owner's open hours minus what is booked, blocked or past.",
        handler=availability,
        input_schema={"type": "object", "properties": {
            "from_date": date, "to_date": date, "timezone": {"type": "string", "description": "IANA zone, optional"},
            "count": {"type": "integer", "minimum": 1, "maximum": MAX_SLOTS}},
            "required": ["from_date", "to_date"], "additionalProperties": False},
        tier=Tier.READ_EXTERNAL, band_required=Band.TRUSTED))
    registry.register(Capability(
        id="windy_calendar.appointments", name="Booked appointments",
        description="Returns the owner's appointments in a date range (default next 7 days) in a timezone (default the owner's): time, booker name, location.",
        handler=appointments,
        input_schema={"type": "object", "properties": {"from_date": date, "to_date": date, "timezone": s},
                      "additionalProperties": False},
        tier=Tier.READ_EXTERNAL, band_required=Band.TRUSTED))
    registry.register(Capability(
        id="windy_calendar.booking_link", name="Booking link",
        description="Returns the public booking-page URL. Customers need no account.",
        handler=booking_link,
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        tier=Tier.READ_EXTERNAL, band_required=Band.TRUSTED))
    registry.register(Capability(
        id="windy_calendar.book", name="Book a time",
        description="Books a slot on the owner's calendar. Atomic: a slot taken at the same instant returns 'just taken' with the next open times; a time that is not an open slot is refused.",
        handler=book,
        input_schema={"type": "object", "properties": {
            "starts_at_utc": s, "booker_name": s, "booker_email": s, "booker_phone": s,
            "booker_note": s, "booker_tz": s},
            "required": ["starts_at_utc", "booker_name", "booker_email"], "additionalProperties": False},
        tier=Tier.EXTERNAL_EFFECT, band_required=Band.TRUSTED, audit_required=True))
    registry.register(Capability(
        id="windy_calendar.block", name="Block time",
        description="Marks a time range unavailable. Overlapping slots are removed from availability until the range passes.",
        handler=block,
        input_schema={"type": "object", "properties": {"starts_at_utc": s, "ends_at_utc": s, "reason": s},
                      "required": ["starts_at_utc", "ends_at_utc"], "additionalProperties": False},
        tier=Tier.EXTERNAL_EFFECT, band_required=Band.TRUSTED, audit_required=True))
    registry.register(Capability(
        id="windy_calendar.create_meeting", name="Set up a meeting",
        description="Asks the owner to approve a meeting with 1 to 10 invitees. Nothing is created and no invite is "
                    "sent until the owner approves it in the Windy Inbox; the answer is that it is waiting there.",
        handler=create_meeting,
        input_schema={"type": "object", "properties": {
            "starts_at_utc": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"},
            "duration_minutes": {"type": "integer", "minimum": 15, "maximum": 240},
            "invitees": {"type": "array", "minItems": 1, "maxItems": 10, "items": {
                "type": "object", "properties": {"email": {"type": "string", "maxLength": 320},
                                                 "name": {"type": "string", "maxLength": 200}},
                "required": ["email"], "additionalProperties": False}},
            "title": {"type": "string", "minLength": 1, "maxLength": 120},
            "note": {"type": "string", "maxLength": 1000, "description": "No links."},
            "location": {"type": "string", "minLength": 1, "maxLength": 500,
                         "description": "A street address or an https link."}},
            "required": ["starts_at_utc", "duration_minutes", "invitees"], "additionalProperties": False},
        # The generic audit row stores the arguments (invitee addresses); this tool logs counts only instead.
        # OWNER only: a sibling must not park meeting requests (with invitee addresses) in the owner's Inbox.
        tier=Tier.EXTERNAL_EFFECT, band_required=Band.OWNER, audit_required=False))
