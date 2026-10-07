"""windy_calendar.* : the owner's Windy Calendar, for windyfly agents (Agentic Calendar K1).

Distinct from the Google-Calendar tools (get_today_events, create_event): these act on the
owner's Windy Calendar through ``windyfly.agent.windy_calendar``. Every tool is owner-only (a
calendar shows someone's life, and appointments carry other people's names and emails). Calendar
itself enforces the band rules, the 20-writes/day cap and the owner's OK for cancel/move; a
``pending_owner`` answer means WAIT, never ask again. The booking link comes ONLY from
``windy_calendar.booking_link``: never type or guess one. Dark: needs WINDY_CALENDAR=1.
"""

from __future__ import annotations

import uuid
from typing import Any

from windyfly.agent import windy_calendar as wc
from windyfly.agent.capabilities.descriptor import Band, Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry

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

    date = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
    s = {"type": "string"}
    registry.register(Capability(
        id="windy_calendar.availability", name="My owner's open times",
        description=(
            "Open times on my owner's Windy Calendar between two dates (YYYY-MM-DD, inclusive), earliest first. "
            "Use for 'find 10 open spots next week'. Pass count for how many. Times come back in UTC and "
            "local; say them in my owner's words and zone."),
        handler=availability,
        input_schema={"type": "object", "properties": {
            "from_date": date, "to_date": date, "timezone": {"type": "string", "description": "IANA zone, optional"},
            "count": {"type": "integer", "minimum": 1, "maximum": MAX_SLOTS}},
            "required": ["from_date", "to_date"], "additionalProperties": False},
        tier=Tier.READ_EXTERNAL, band_required=Band.OWNER))
    registry.register(Capability(
        id="windy_calendar.appointments", name="My owner's booked appointments",
        description="Booked appointments on my owner's Windy Calendar (optional date range). Contains other people's names; owner only.",
        handler=appointments,
        input_schema={"type": "object", "properties": {"from_date": date, "to_date": date, "timezone": s},
                      "additionalProperties": False},
        tier=Tier.READ_EXTERNAL, band_required=Band.OWNER))
    registry.register(Capability(
        id="windy_calendar.booking_link", name="My owner's booking link",
        description=("The link where anyone can pick a time with my owner. ALWAYS get it from this tool; "
                     "never type, guess or edit a booking link."),
        handler=booking_link,
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        tier=Tier.READ_EXTERNAL, band_required=Band.OWNER))
    registry.register(Capability(
        id="windy_calendar.book", name="Book a time on my owner's calendar",
        description=(
            "Book an appointment on my owner's calendar when my owner asks. Use a time from "
            "windy_calendar.availability (starts_at_utc exactly as given). If the answer says the slot was "
            "taken, offer the next_slots. Never claim it is booked unless the result says so."),
        handler=book,
        input_schema={"type": "object", "properties": {
            "starts_at_utc": s, "booker_name": s, "booker_email": s, "booker_phone": s,
            "booker_note": s, "booker_tz": s},
            "required": ["starts_at_utc", "booker_name", "booker_email"], "additionalProperties": False},
        tier=Tier.EXTERNAL_EFFECT, band_required=Band.OWNER, audit_required=True))
    registry.register(Capability(
        id="windy_calendar.block", name="Block time on my owner's calendar",
        description="Block my owner's own time (up to seven days) when they ask, for example 'block Friday afternoon'.",
        handler=block,
        input_schema={"type": "object", "properties": {"starts_at_utc": s, "ends_at_utc": s, "reason": s},
                      "required": ["starts_at_utc", "ends_at_utc"], "additionalProperties": False},
        tier=Tier.EXTERNAL_EFFECT, band_required=Band.OWNER, audit_required=True))
