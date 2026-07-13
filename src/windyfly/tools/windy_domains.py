"""Windy Cloud Domains tools — the agent's hands on the registrar cell.

Talks to windy-cloud-domains over HTTPS (same-origin at
cloud.windycloud.com/api/v1/domains, or WINDY_DOMAINS_URL for dev). Design
per the ecosystem tool conventions:

- Env-gated: unset base URL / EPT → tools return a structured
  ``{"status": "unavailable", ...}`` so registration always succeeds and
  only execution surfaces missing config.
- EPT bearer: the agent authenticates with its Eternitas passport token —
  the cell throttles by EI band, never by omission.
- Never raise into the loop: every error becomes a dict the LLM can
  explain to grandma.
- The purchase rail is CONFIRM_FLOW: ``buy_domain`` returns a
  ``confirm_required`` question to RELAY VERBATIM; ``confirm_purchase``
  completes it only after the human's yes.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0


def _base_url() -> str:
    return os.environ.get(
        "WINDY_DOMAINS_URL", "https://cloud.windycloud.com/api/v1/domains"
    ).rstrip("/")


def _ept() -> str:
    return os.environ.get("ETERNITAS_PASSPORT_TOKEN", "").strip()


def is_configured() -> bool:
    return bool(_ept())


def _unavailable() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "message": "Windy Cloud Domains isn't connected for this helper yet "
        "(no Eternitas passport token). Tell the owner to finish setup.",
    }


def _request(method: str, path: str, **kw: Any) -> dict[str, Any]:
    if not is_configured():
        return _unavailable()
    try:
        r = httpx.request(
            method,
            f"{_base_url()}{path}",
            headers={"Authorization": f"Bearer {_ept()}"},
            timeout=_TIMEOUT,
            follow_redirects=True,
            **kw,
        )
    except httpx.HTTPError as e:
        logger.warning("windy-domains %s %s failed: %s", method, path, e)
        return {"error": "Windy Cloud Domains isn't reachable right now.", "detail": str(e)}
    if r.status_code >= 400:
        # The cell's errors are already repair pointers (code/speak/…).
        try:
            body = r.json()
        except Exception:
            body = {"error": r.text[:200]}
        detail = body.get("detail", body)
        return {"failed": True, "status_code": r.status_code, **(
            detail if isinstance(detail, dict) else {"error": str(detail)}
        )}
    return r.json()


def search_domain(fqdn: str) -> dict[str, Any]:
    """Check if one exact domain name is available and its price."""
    return _request("GET", "/search", params={"q": fqdn})


def suggest_domains(context_hints: list[str]) -> dict[str, Any]:
    """Brainstorm available names from personal hints (a name, a nickname,
    a business word). Hints are never stored."""
    return _request("POST", "/suggest", json={"context_hints": context_hints})


def buy_domain(fqdn: str, idempotency_key: str, site_ref: str = "") -> dict[str, Any]:
    """Start a purchase on the owner's saved card. Returns confirm_required
    + a question to RELAY VERBATIM; then call confirm_purchase after a yes.
    Pass site_ref to bundle buy+connect into that ONE yes."""
    body: dict[str, Any] = {"fqdn": fqdn, "idempotency_key": idempotency_key}
    if site_ref:
        body["site_ref"] = site_ref
    return _request("POST", "/purchase", json=body)


def confirm_purchase(order_id: str, confirm_token: str) -> dict[str, Any]:
    """Complete a purchase AFTER the human said yes. The charge happens here."""
    return _request(
        "POST", "/confirm", json={"order_id": order_id, "confirm_token": confirm_token}
    )


def list_my_domains() -> dict[str, Any]:
    """The domains this account owns."""
    return _request("GET", "")


def set_auto_renew(domain_id: str, enabled: bool, confirm_token: str = "") -> dict[str, Any]:
    """Turn yearly auto-renew on/off. Returns confirm_required first (turning
    it OFF can lose the domain a year later) — relay the question."""
    body: dict[str, Any] = {"enabled": enabled}
    if confirm_token:
        body["confirm_token"] = confirm_token
    return _request("POST", f"/{domain_id}/auto-renew", json=body)


def register_windy_domains_tools(registry: ToolRegistry) -> None:
    registry.register(
        name="search_domain",
        description=(
            "Check if ONE exact domain name is available and what it costs "
            "per year. Use for 'is grandmarose.com taken?'. Safe; charges "
            "nothing. Returns { fqdn, available, price_cents?, speak }."
        ),
        parameters={
            "type": "object",
            "properties": {"fqdn": {"type": "string", "description": "e.g. grandmarose.com"}},
            "required": ["fqdn"],
        },
        fn=lambda fqdn: search_domain(fqdn),
    )
    registry.register(
        name="suggest_domains",
        description=(
            "Brainstorm available domain names from 1-8 personal hints (a "
            "granddaughter's name, a nickname, a business word). Hints are "
            "never stored. Returns { suggestions: [{fqdn, price_cents, "
            "speak}], speak }."
        ),
        parameters={
            "type": "object",
            "properties": {
                "context_hints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Up to 8 short hints from the conversation.",
                }
            },
            "required": ["context_hints"],
        },
        fn=lambda context_hints: suggest_domains(context_hints),
    )
    registry.register(
        name="buy_domain",
        description=(
            "Start buying a domain on the owner's saved card. ALWAYS returns "
            "confirm_required with a question — RELAY IT TO THE HUMAN "
            "VERBATIM and call confirm_purchase only after they say yes (a "
            "spoken yes counts). Pass site_ref to also connect it to a site "
            "in the same one yes. WARNING: real money once confirmed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "fqdn": {"type": "string"},
                "idempotency_key": {
                    "type": "string",
                    "description": "A stable unique key for this attempt (e.g. a uuid).",
                },
                "site_ref": {"type": "string", "description": "Optional site id to bundle-connect."},
            },
            "required": ["fqdn", "idempotency_key"],
        },
        fn=lambda fqdn, idempotency_key, site_ref="": buy_domain(fqdn, idempotency_key, site_ref),
    )
    registry.register(
        name="confirm_purchase",
        description=(
            "Complete a domain purchase AFTER the human said yes to the "
            "buy_domain question. The card is charged here. Returns "
            "{ order_id, state, speak }."
        ),
        parameters={
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "confirm_token": {"type": "string"},
            },
            "required": ["order_id", "confirm_token"],
        },
        fn=lambda order_id, confirm_token: confirm_purchase(order_id, confirm_token),
    )
    registry.register(
        name="list_my_domains",
        description="List the domains this account owns. Returns { domains, speak }.",
        parameters={"type": "object", "properties": {}},
        fn=lambda: list_my_domains(),
    )
    registry.register(
        name="set_auto_renew",
        description=(
            "Turn yearly auto-renew on or off for a domain. Returns "
            "confirm_required first — RELAY the question; turning it OFF can "
            "lose the domain later."
        ),
        parameters={
            "type": "object",
            "properties": {
                "domain_id": {"type": "string"},
                "enabled": {"type": "boolean"},
                "confirm_token": {"type": "string"},
            },
            "required": ["domain_id", "enabled"],
        },
        fn=lambda domain_id, enabled, confirm_token="": set_auto_renew(
            domain_id, enabled, confirm_token
        ),
    )
