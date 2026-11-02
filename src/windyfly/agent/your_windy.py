"""Your Windy: the facts this agent has about its own Windy platforms, rendered per windy-contracts
schema/agent/your-windy.v1.json (Grant 2026-11-02: never born clueless; facts only).

DARK: nothing is read or added to the prompt unless WINDY_YOUR_WINDY=1. Facts are read LIVE (never copied at birth)
and kept for the session (SESSION_TTL_S); a source that doesn't answer is simply left out (the contract's Y3: unknown is
omitted, never guessed). Sources wired today: Eternitas's public registry (status, trust score), Windy Mail's mailbox
(own address), the confirmed Matrix id (own chat handle). Vault readiness, activated platforms,
plan, Cloud quota and Search allowance wait for their owners' reads (ONE-WINDY plan section D). Never raises.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

ENV_FLAG = "WINDY_YOUR_WINDY"
SESSION_TTL_S = 1800.0
_TIMEOUT_S = 4.0
_MB = 1024 * 1024

# x-platform-names of your-windy.v1 (the dashboard's names).
PLATFORM_NAMES: dict[str, str] = {
    "windy-word": "Windy Word", "windy-chat": "Windy Chat", "windy-mail": "Windy Mail", "windy-cloud": "Windy Cloud",
    "windy-fly": "Windy Fly", "windy-word-mobile": "Windy Word Mobile", "windy-clone": "Windy Clone",
    "windy-traveler": "Windy Traveler", "windy-code": "Windy Code", "eternitas": "Eternitas", "windy-text": "Windy Text",
    "windy-call": "Windy Call", "windy-mobile": "Windy Mobile", "windy-talk": "Windy Talk", "windy-mind": "Windy Mind",
    "windy-search": "Windy Search", "windy-hand": "Windy Hand", "windy-vault": "Windy Vault",
    "windy-connect": "Windy Connect", "windy-drops": "Windy Drops", "windy-calendar": "Windy Calendar",
    "windy-git": "Windy Git", "windy-translate": "Windy Translate",
}

# your-windy.v1 field rules (Y8): every string is ONE line. A value that fails is OMITTED, never cleaned or shortened:
# the block is pasted into the model's context, so a newline in a field could forge a line (Hub, 11-02). fullmatch,
# never search/match: Python's "$" also matches before a trailing newline.
FIELD_PATTERNS: dict[str, str] = {
    "name": r"^[^\x00-\x1f\x7f\u2028\u2029]{1,80}$",
    "passport": r"^ET[0-9]{2}-[A-Z0-9-]{4,40}$",
    "email": r"^[^\x00-\x20\x7f\u2028\u2029@]{1,64}@[^\x00-\x20\x7f\u2028\u2029@]{1,255}$",
    "chat_handle": r"^@[^:\s\x00-\x1f\x7f\u2028\u2029]{1,255}:[^\s\x00-\x1f\x7f\u2028\u2029]{1,255}$",
    "plan": r"^[^\x00-\x1f\x7f\u2028\u2029]{1,40}$",
}
PASSPORT_STATUSES = ("active", "suspended", "revoked", "not_born")
CAP = 1200  # Y7 / R9


def _valid(field: str, value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(FIELD_PATTERNS[field], value) is not None


def _clean_agent(a: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {k: a[k] for k in ("name", "passport", "email", "chat_handle") if _valid(k, a.get(k))}
    if a.get("passport_status") in PASSPORT_STATUSES:
        out["passport_status"] = a["passport_status"]
    ts = a.get("trust_score")
    if isinstance(ts, int) and not isinstance(ts, bool) and 0 <= ts <= 1000:
        out["trust_score"] = ts
    return out


_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "doc": None}


def enabled() -> bool:
    return os.environ.get(ENV_FLAG, "") == "1"


def _size(n: int) -> str:
    return f"{n // _MB} MB" if n < 1024 * _MB else f"{n / (1024 * _MB):.1f} GB"


def render(doc: dict[str, Any]) -> str:
    """your-windy.v1 x-render, R1-R8. Byte for byte the contract's x-examples (tested against the vendored file)."""
    a = _clean_agent(doc.get("agent") or {})
    e: dict[str, Any] = doc.get("entitlements") or {}
    plan = doc.get("plan") if _valid("plan", doc.get("plan")) else None
    out = [f"YOUR WINDY (facts, read {doc['read_at_utc']})"]
    if a.get("passport"):
        bits = [x for x in (a.get("passport_status"),
                            f"trust score {a['trust_score']}" if a.get("trust_score") is not None else None) if x]
        out.append(f"You: {a.get('name') or 'this agent'}. Eternitas passport {a['passport']}"
                   + (f" ({', '.join(bits)})." if bits else "."))
    if a.get("email"):
        out.append(f"Your email: {a['email']}")
    if a.get("chat_handle"):
        out.append(f"Your Windy Chat handle: {a['chat_handle']}")
    cl: dict[str, Any] = e.get("cloud") or {}
    parts = []
    if "agent_allowance_bytes" in cl:
        parts.append(f"{_size(cl['agent_allowance_bytes'])} for you")
    if "person_quota_bytes" in cl and "person_used_bytes" in cl:
        who = f"Your owner's {plan} plan" if plan else "Your owner's plan"
        parts.append(f"{who}: {_size(cl['person_used_bytes'])} of {_size(cl['person_quota_bytes'])} used")
    if parts:
        out.append("Windy Cloud storage: " + ". ".join(parts) + ".")
    vault: dict[str, Any] = e.get("vault") or {}
    if "ready" in vault:
        out.append("Windy Vault: ready." if vault["ready"] else "Windy Vault: not set up yet.")
    s: dict[str, Any] = e.get("search") or {}
    if "monthly_allowance" in s and "used_this_month" in s:
        out.append(f"Windy Search: {s['used_this_month']} of {s['monthly_allowance']} searches used this month.")
    groups: dict[str, list[str]] = {"Activated for your owner": [], "Your owner can activate": [], "In preview": [],
                                    "Coming soon (not usable yet)": []}
    for p in doc.get("platforms") or []:
        name = PLATFORM_NAMES.get(str(p.get("id")))
        if not name:
            continue
        if p.get("status") == "coming_soon":
            groups["Coming soon (not usable yet)"].append(name)
        elif p.get("activated"):
            groups["Activated for your owner"].append(name)
        elif p.get("status") in ("active", "available"):
            groups["Your owner can activate"].append(name)
        else:
            groups["In preview"].append(name)
    head = len(out)
    for label, names in groups.items():
        if names:
            out.append(f"{label}: {', '.join(sorted(names))}.")
    while len("\n".join(out)) > CAP and len(out) > head:  # R9: drop platform lines from the end, never cut a line
        out.pop()
    return "\n".join(out)


# ── live sources (each returns facts or {} ; never raises) ─────────────────────────────────────────────────────────


def _eternitas(passport: str) -> dict[str, Any]:
    import httpx

    from windyfly.eternitas.url import resolve_eternitas_url

    try:
        r = httpx.get(f"{resolve_eternitas_url('https://api.eternitas.ai').rstrip('/')}/api/v1/registry/verify/{passport}",
                      timeout=_TIMEOUT_S)
        d = r.json() if r.status_code == 200 else {}
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, Any] = {}
    if isinstance(d, dict):
        if d.get("status") in ("active", "suspended", "revoked", "not_born"):
            out["passport_status"] = d["status"]
        if isinstance(d.get("trust_score"), int):
            out["trust_score"] = d["trust_score"]
    return out


def _mail_address() -> str:
    try:
        from windyfly.tools.mail import my_mailbox

        box = my_mailbox()
        addr = box.get("address") if isinstance(box, dict) else None
        return addr if isinstance(addr, str) and "@" in addr else ""
    except Exception:  # noqa: BLE001
        return ""


def _chat_handle() -> str:
    try:
        from windyfly.channels.matrix_self import own_matrix_id

        return own_matrix_id() or ""
    except Exception:  # noqa: BLE001
        return ""


def build_document(agent_name: str = "") -> dict[str, Any]:
    passport = os.environ.get("ETERNITAS_PASSPORT", "").strip()
    if not agent_name:
        try:
            from windyfly.tools.outbound_identity import agent_name as _name

            agent_name = _name()
        except Exception:  # noqa: BLE001
            agent_name = ""
    agent: dict[str, Any] = {"name": agent_name or "this agent"}
    if passport:
        agent["passport"] = passport
        agent.update(_eternitas(passport))
    email = _mail_address()
    if email:
        agent["email"] = email
    handle = _chat_handle()
    if handle:
        agent["chat_handle"] = handle
    doc: dict[str, Any] = {"v": 1, "read_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "agent": agent, "platforms": []}
    # Vault: not read here. Only the owner's Vault tools may import the Vault client (strand N8), and Vault's
    # capabilities "none" can mean no Vault OR saving switched off, so there is no clear fact yet; the Vault line
    # waits for a clear Vault field through an allowed path (unknown = omitted, the contract's Y3).
    return doc


def block(agent_name: str = "") -> str:
    """The rendered facts block for the prompt, or "" when dark. Read once per session (SESSION_TTL_S)."""
    if not enabled():
        return ""
    try:
        with _lock:
            now = time.monotonic()
            if _cache["doc"] is None or now - _cache["at"] > SESSION_TTL_S:
                _cache["doc"], _cache["at"] = build_document(agent_name), now
            return render(_cache["doc"])
    except Exception as exc:  # noqa: BLE001  (a facts block never breaks a turn)
        logger.warning("your-windy: not rendered (%s)", type(exc).__name__)
        return ""


def reset_cache() -> None:
    with _lock:
        _cache["doc"], _cache["at"] = None, 0.0
