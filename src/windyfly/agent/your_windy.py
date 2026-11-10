"""Your Windy: the facts this agent has about its own Windy platforms, rendered per windy-contracts
schema/agent/your-windy.v1.json (Grant 2026-11-02: never born clueless; facts only).

DARK: nothing is read or added to the prompt unless WINDY_YOUR_WINDY=1. Facts are read LIVE (never copied at birth)
and kept for the session (SESSION_TTL_S); a source that doesn't answer is simply left out (the contract's Y3: unknown is
omitted, never guessed). Sources wired today: Eternitas's public registry (status, trust score), Windy Mail's mailbox
(own address), the confirmed Matrix id (own chat handle), and (DARK behind WINDY_MY_WINDY=1 as well) the owner's plan
and platforms from Windy Accounts' ONE "my Windy" read (windy-contracts schema/hub/my-windy.v1.json). Vault readiness,
Cloud quota and Search allowance wait for their owners' reads (ONE-WINDY plan section D). Never raises.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import re
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

ENV_FLAG = "WINDY_YOUR_WINDY"
SESSION_TTL_S = 1800.0
_TIMEOUT_S = 2.0
# All live reads together get at most this long, once per session (Boss 11-02: a slow source never costs a turn more
# than ~2 s, and only on the session's first turn). A source that hasn't answered by then is omitted.
READ_BUDGET_S = 2.0
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


def _count(v: Any) -> bool:
    """A numeric fact (bytes, searches): a non-negative int, never a bool, float or text; else omitted (Y8, Hub)."""
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


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
    cl: dict[str, Any] = {k: v for k, v in (e.get("cloud") or {}).items() if _count(v)}
    parts = []
    if "agent_allowance_bytes" in cl:
        parts.append(f"{_size(cl['agent_allowance_bytes'])} for you")
    if "person_quota_bytes" in cl and "person_used_bytes" in cl:
        who = f"Your owner's {plan} plan" if plan else "Your owner's plan"
        parts.append(f"{who}: {_size(cl['person_used_bytes'])} of {_size(cl['person_quota_bytes'])} used")
    if parts:
        out.append("Windy Cloud storage: " + ". ".join(parts) + ".")
    vault: dict[str, Any] = e.get("vault") or {}
    if isinstance(vault.get("ready"), bool):
        out.append("Windy Vault: ready." if vault["ready"] else "Windy Vault: not set up yet.")
    s: dict[str, Any] = {k: v for k, v in (e.get("search") or {}).items() if _count(v)}
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


# ── my-windy.v1: the owner's plan + platforms from Windy Accounts (Hub; DARK) ─────────────────────────────────────────
#
# GET {WINDY_HUB_URL}/api/v1/identity/my-windy. The agent sends mode B when it can mint (EPT+agent, aud windy-hub, a
# fresh DPoP proof: the ONLY form a #700-paired computer holds), else its stored EPT as a bearer; the contract takes
# either. Its own 2 s timeout and the session's READ_BUDGET_S apply. Every non-200 (401 unauthorized /
# ept_before_cutoff / agent_retired, 403 agent_unlinked / account_deactivated, 404 = the read switched off) means the
# plan and platforms are OMITTED (Y3), never guessed. The answer is checked WHOLE against my-windy.v1: one field or
# value outside it (an extra field could carry a person's detail, M4; a duplicate id, M6) and the whole read is
# omitted. The account server's own switch is MY_WINDY_ENABLED (unset = 404).
MY_WINDY_FLAG = "WINDY_MY_WINDY"
MY_WINDY_AUD = "windy-hub"
MY_WINDY_PATH = "/api/v1/identity/my-windy"
PLATFORM_STATUSES = ("active", "available", "preview", "developer_api", "coming_soon")
_MY_WINDY_KEYS = frozenset({"v", "read_at_utc", "plan", "platforms"})
_PLATFORM_KEYS = frozenset({"id", "status", "activated"})
_READ_AT = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
_CODE = re.compile(r"[a-z][a-z0-9_]{0,40}")


def my_windy_enabled() -> bool:
    return enabled() and os.environ.get(MY_WINDY_FLAG, "") == "1"


def my_windy_facts(body: Any) -> dict[str, Any] | None:
    """The plan + platforms of a my-windy.v1 answer, or None when ANY part of it is outside the contract."""
    if not isinstance(body, dict) or set(body) - _MY_WINDY_KEYS or body.get("v") != 1 or isinstance(body.get("v"), bool):
        return None
    if not (isinstance(body.get("read_at_utc"), str) and re.fullmatch(_READ_AT, body["read_at_utc"])):
        return None
    platforms = body.get("platforms")
    if not isinstance(platforms, list) or len(platforms) > 40:
        return None
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in platforms:
        if not isinstance(p, dict) or set(p) != _PLATFORM_KEYS or not isinstance(p["id"], str):
            return None
        if p["id"] not in PLATFORM_NAMES or p["id"] in seen:
            return None
        if p["status"] not in PLATFORM_STATUSES or not isinstance(p["activated"], bool):
            return None
        seen.add(p["id"])
        out.append({"id": p["id"], "status": p["status"], "activated": p["activated"]})
    facts: dict[str, Any] = {"platforms": out}
    if "plan" in body:
        if not _valid("plan", body["plan"]):
            return None
        facts["plan"] = body["plan"]
    return facts


def _refusal_code(resp: Any) -> str:
    """A short refusal code from the body for the log (code / error / detail), or "": never other body text."""
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return ""
    for key in ("code", "error", "detail"):
        v = body.get(key) if isinstance(body, dict) else None
        if isinstance(v, str) and _CODE.fullmatch(v):
            return v
    return ""


def _my_windy() -> dict[str, Any]:
    import httpx

    from windyfly.agent import service_auth
    from windyfly.hub_login import hub_url

    url = hub_url() + MY_WINDY_PATH
    form = "mode B"
    try:
        headers = service_auth.agent_headers(MY_WINDY_AUD, "GET", url)
    except service_auth.ServiceAuthError:
        ept = (os.environ.get("ETERNITAS_PASSPORT_TOKEN", "").strip()
               or os.environ.get("WINDY_PASSPORT_EPT", "").strip())
        if not ept:
            return {}
        headers, form = {"Authorization": f"Bearer {ept}"}, "stored EPT"
    try:
        resp = httpx.get(url, headers=headers, timeout=_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        logger.info("your-windy: my-windy not read (%s); plan + platforms omitted", type(exc).__name__)
        return {}
    if resp.status_code != 200:
        if resp.status_code == 401 and form == "mode B":
            service_auth.forget_token()  # the next session mints afresh
        logger.info("your-windy: my-windy answered %s %s (%s); plan + platforms omitted", resp.status_code,
                    _refusal_code(resp) or "no code", form)
        return {}
    try:
        body = resp.json()
    except ValueError:
        body = None
    facts = my_windy_facts(body)
    if facts is None:
        logger.warning("your-windy: my-windy answer is outside my-windy.v1; plan + platforms omitted")
        return {}
    return facts


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
    # The live reads run in parallel under ONE deadline (READ_BUDGET_S). A read still running at the deadline is
    # abandoned (its thread finishes in the background and is ignored) and its fact is omitted.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="your-windy")
    jobs: dict[str, concurrent.futures.Future[Any]] = {"mail": pool.submit(_mail_address), "chat": pool.submit(_chat_handle)}
    if passport:
        jobs["eternitas"] = pool.submit(_eternitas, passport)
    if my_windy_enabled():
        jobs["my_windy"] = pool.submit(_my_windy)
    done, _ = concurrent.futures.wait(list(jobs.values()), timeout=READ_BUDGET_S)
    pool.shutdown(wait=False, cancel_futures=True)

    def got(key: str) -> Any:
        f = jobs.get(key)
        if f is None or f not in done:
            return None
        try:
            return f.result()
        except Exception:  # noqa: BLE001
            return None

    agent.update(got("eternitas") or {})
    email = got("mail")
    if email:
        agent["email"] = email
    handle = got("chat")
    if handle:
        agent["chat_handle"] = handle
    doc: dict[str, Any] = {"v": 1, "read_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "agent": agent, "platforms": []}
    mine = got("my_windy") or {}
    if mine.get("platforms"):
        doc["platforms"] = mine["platforms"]
    if mine.get("plan"):
        doc["plan"] = mine["plan"]
    # Vault: not read here. Only the owner's Vault tools may import the Vault client (strand N8), and Vault's
    # capabilities "none" can mean no Vault OR saving switched off, so there is no clear fact yet; the Vault line
    # waits for a clear Vault field through an allowed path (unknown = omitted, the contract's Y3).
    return doc


def _session_doc(agent_name: str = "") -> dict[str, Any]:
    with _lock:
        now = time.monotonic()
        if _cache["doc"] is None or now - _cache["at"] > SESSION_TTL_S:
            _cache["doc"], _cache["at"] = build_document(agent_name), now
        doc: dict[str, Any] = _cache["doc"]
        return doc


def block(agent_name: str = "") -> str:
    """The rendered facts block for the prompt, or "" when dark. Read once per session (SESSION_TTL_S)."""
    if not enabled():
        return ""
    try:
        return render(_session_doc(agent_name))
    except Exception as exc:  # noqa: BLE001  (a facts block never breaks a turn)
        logger.warning("your-windy: not rendered (%s)", type(exc).__name__)
        return ""


# ── suggestions (Grant 2026-11-02 ~19:25Z, "the agent needs to suggest all that"; Boss's bounds 19:31Z) ───────────────
# The facts block stays facts only. Next to it, a SHORT rule: offer a Windy feature only when it fits what the owner is
# doing right now, each at most once per conversation, free features only, only features the facts say are usable, and
# "stop suggesting" sticks. DARK: needs WINDY_SUGGEST_FEATURES=1 as well as WINDY_YOUR_WINDY=1.

SUGGEST_FLAG = "WINDY_SUGGEST_FEATURES"
SUGGESTIONS_KEY = "windy_suggestions"  # soul row per owner: "off" after "stop suggesting", "on" after "start"
# (platform id, when it fits). Only these, in this order.
SUGGESTIONS: tuple[tuple[str, str], ...] = (
    ("windy-vault", "a password, API key or other login is pasted or mentioned"),
    ("windy-cloud", "there is a file they want to keep"),
    ("windy-calendar", "they are booking or planning a time"),
)
# The WHOLE message must be the command (Hub on #692): "tell my boss to stop suggesting meetings" is not one.
_POLITE = r"(?:(?:ok(?:ay)?|please|pls|hey|so)[\s,]+)?"
_TAIL = r"(?:\s+(?:things|stuff|anything|features|that|those|them|again))?(?:[\s,]+please)?[\s.!]*"
_STOP = re.compile(_POLITE + r"(?:(?:stop|quit|no\s+more)\s+suggest(?:ing|ions)?|don['’]?t\s+suggest(?:\s+anything)?)" + _TAIL,
                   re.IGNORECASE)
_START = re.compile(_POLITE + r"(?:(?:start|resume)\s+suggesting|you\s+can\s+suggest)" + _TAIL, re.IGNORECASE)


def suggestions_enabled() -> bool:
    return enabled() and os.environ.get(SUGGEST_FLAG, "") == "1"


def _usable(doc: dict[str, Any]) -> set[str]:
    """Platform ids the facts say the owner has or can turn on now (never coming soon, never in preview)."""
    out = set()
    for p in doc.get("platforms") or []:
        if isinstance(p, dict) and p.get("status") != "coming_soon" and (
                p.get("activated") is True or p.get("status") in ("active", "available")):
            out.add(str(p.get("id")))
    return out


def _already_offered(db: Any, session_id: str) -> set[str]:
    """Features this conversation's earlier replies already named (once per feature per conversation)."""
    rows = db.fetchall("SELECT content FROM episodes WHERE session_id = ? AND role = 'assistant' "
                       "ORDER BY created_at DESC, rowid DESC LIMIT 200", (session_id,))
    said = "\n".join(str(r.get("content") or "") for r in rows).lower()
    return {pid for pid, _ in SUGGESTIONS if PLATFORM_NAMES[pid].lower() in said}


def _owner_switch(db: Any, user_message: str) -> str | None:
    """Stores the owner's "stop suggesting" / "start suggesting" and returns the current setting."""
    from windyfly.memory.soul import get_soul, upsert_soul

    said = (user_message or "").strip()
    if _STOP.fullmatch(said):
        upsert_soul(db, SUGGESTIONS_KEY, "off", source="owner")
    elif _START.fullmatch(said):
        upsert_soul(db, SUGGESTIONS_KEY, "on", source="owner")
    row = get_soul(db, SUGGESTIONS_KEY)
    return str(row["value"]) if row else None


def suggestion_block(db: Any, session_id: str, user_message: str, agent_name: str = "") -> str:
    """The suggestion rule for the OWNER's prompt, or "" (dark, switched off by the owner, or nothing left to offer).
    Never raises."""
    if not suggestions_enabled():
        return ""
    try:
        if _owner_switch(db, user_message) == "off":
            return ""
        doc = _session_doc(agent_name)
        usable, offered = _usable(doc), _already_offered(db, session_id)
        cloud_facts = any(_count(v) for v in ((doc.get("entitlements") or {}).get("cloud") or {}).values())
        lines = []
        for pid, when in SUGGESTIONS:
            if pid in usable and pid not in offered:
                extra = " (say the free space shown in YOUR WINDY)" if pid == "windy-cloud" and cloud_facts else ""
                lines.append(f"- {when} -> {PLATFORM_NAMES[pid]}{extra}")
        if not lines:
            return ""
        return ("SUGGESTIONS (your owner may not know these Windy features exist). Offer one ONLY when it fits what "
                "they are doing right now, in one short sentence:\n" + "\n".join(lines) + "\n"
                "Each at most once in this conversation, and never again after a no. Only these features, and only "
                "what YOUR WINDY says about them. Never push a paid plan unless they ask about plans.")
    except Exception as exc:  # noqa: BLE001  (a suggestion never breaks a turn)
        logger.warning("your-windy: no suggestions (%s)", type(exc).__name__)
        return ""


def reset_cache() -> None:
    with _lock:
        _cache["doc"], _cache["at"] = None, 0.0
