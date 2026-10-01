"""Windy Mind client helper (MD14): when Mind is down or slow, degrade, never strand.

SOURCE OF TRUTH: windy-mind/clients/python/mind_client.py. Consumers VENDOR this
single file and run a drift test against it (no forks). Spec:
windy-orchestra/specs/MIND_ROUTE_TABLE_MD14.md. Dependencies: httpx, PyJWT[crypto].

What it does, in one place (so two retry loops never stack):
  * calls Mind's /v1/chat with hard timeouts and ONE jittered retry on transient
    failures (connect error, timeout, 502/503/504);
  * keeps the signed route table (GET /v1/route-table, verified with Mind's JWKS)
    and fails CLOSED: any verification or shape problem keeps the LAST GOOD table;
  * on a Mind outage walks the floors: the authenticated standby (Mind's degraded
    standby, same token), then a caller-supplied local floor; every fallback is
    MARKED in the return value (never silent); with no floor it raises
    MindUnavailableError (an honest "no brain", never an empty reply);
  * never retries or falls back on a refusal (4xx: policy, switched-off model, rate
    limit): a refusal is a wall, not an outage;
  * reads the caller's switched-off models from EVERY successful reply
    (x-mind-switched-off) and from the table, and refuses to trust an old list.

It never holds a provider key and never calls a provider directly. Streaming is
not covered in v1.
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt

DEFAULT_BASE_URL = "https://api.windymind.ai"
SCHEMA_VERSION = 1
#: Mirror of contracts/route-table.v1.json "required". A drift test compares them.
REQUIRED_KEYS = (
    "v", "iat", "ttl_s", "stale_ok_s", "switched_off_ttl_s", "switched_off_stale_ok_s",
    "degraded", "caller", "named_model_fallback", "chains", "floors", "switched_off",
)
_TRANSIENT_STATUS = {502, 503, 504}


class MindError(Exception):
    """Base class."""


class MindUnavailableError(MindError):
    """Mind is down/slow and no floor worked: tell the person honestly."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"mind unavailable ({reason}): {detail}".strip())
        self.reason = reason  # "mind_down" | "mind_slow"


class MindRefusedError(MindError):
    """Mind answered with a refusal (4xx). Never retried, never fallen back."""

    def __init__(self, status: int, error_code: str | None, detail: str, headers: dict[str, str]) -> None:
        super().__init__(f"mind refused {status} ({error_code or 'no code'}): {detail}")
        self.status = status
        self.error_code = error_code  # e.g. "model_disabled"
        self.headers = headers


@dataclass
class MindResult:
    response: dict[str, Any]
    served_model: str | None
    #: None when Mind served it; "mind_down" | "mind_slow" when a floor did.
    fallback: str | None = None
    #: None | "standby" | "local"
    floor: str | None = None
    switched_off: list[str] = field(default_factory=list)


Token = str | Callable[[], str]


class MindClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: Token = "",
        *,
        local_floor: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        connect_timeout: float = 2.0,
        read_timeout: float = 30.0,
        retries: int = 1,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        # MIND_BASE_URL override: lets Cloud's drill point at a dead/503 stub.
        self.base_url = (base_url or os.environ.get("MIND_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._token = token
        self._local_floor = local_floor
        self._timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        self._retries = max(0, retries)
        self._http = httpx.AsyncClient(transport=transport)
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self.table: dict[str, Any] | None = None
        self._table_at = 0.0
        self._jwks: dict[str, Any] = {}
        self._off: list[str] = []
        self._off_at: float | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    # ── auth ─────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        tok = self._token() if callable(self._token) else self._token
        return {"Authorization": f"Bearer {tok}"} if tok else {}

    # ── the signed route table ───────────────────────────────────────
    async def _load_jwks(self) -> None:
        r = await self._http.get(f"{self.base_url}/.well-known/mind-jwks.json", timeout=3.0)
        r.raise_for_status()
        self._jwks = {k["kid"]: k for k in r.json().get("keys", [])}

    def _verify(self, token: str, kid: str) -> dict[str, Any]:
        jwk = self._jwks.get(kid)
        if jwk is None:
            raise ValueError("unknown kid")
        table = jwt.decode(token, jwt.PyJWK(jwk).key, algorithms=["ES256"], options={"verify_aud": False})
        missing = [k for k in REQUIRED_KEYS if k not in table]
        if missing or table.get("v") != SCHEMA_VERSION:
            raise ValueError("route table shape")
        if any(not f.get("requires_auth") for f in table.get("floors", [])):
            raise ValueError("unauthenticated floor refused")
        return table

    async def refresh_table(self) -> bool:
        """Fetch + verify the table. True on success. On ANY failure the LAST GOOD
        table is kept (fail closed) and False is returned."""
        try:
            r = await self._http.get(f"{self.base_url}/v1/route-table", headers=self._headers(), timeout=3.0)
            r.raise_for_status()
            body = r.json()
            kid = body["kid"]
            if kid not in self._jwks:
                await self._load_jwks()
            table = self._verify(body["jws"], kid)
        except Exception:
            return False
        self.table = table
        self._table_at = self._clock()
        self._set_off(table["switched_off"])
        return True

    def _table_usable(self) -> bool:
        return self.table is not None and (self._clock() - self._table_at) <= self.table["stale_ok_s"]

    def _set_off(self, models: list[str]) -> None:
        self._off = sorted(models)
        self._off_at = self._clock()

    def switched_off(self) -> list[str] | None:
        """The caller's switched-off models, or None (UNKNOWN) when the list is older
        than the table's switched_off_stale_ok_s. Callers must treat a named premium
        model as unknown, not as 'on', when this is None and Mind is unreachable."""
        if self._off_at is None:
            return None
        limit = (self.table or {}).get("switched_off_stale_ok_s", 300)
        return list(self._off) if (self._clock() - self._off_at) <= limit else None

    # ── chat ─────────────────────────────────────────────────────────
    async def chat(self, body: dict[str, Any]) -> MindResult:
        if self.table is None or (self._clock() - self._table_at) > self.table["ttl_s"]:
            await self.refresh_table()  # best effort; never blocks the call on failure

        last_error = ""
        slow = False
        for attempt in range(self._retries + 1):
            try:
                r = await self._http.post(
                    f"{self.base_url}/v1/chat", json=body, headers=self._headers(), timeout=self._timeout
                )
            except (httpx.TimeoutException, httpx.TransportError) as e:
                slow = isinstance(e, httpx.TimeoutException)
                last_error = type(e).__name__
            else:
                if r.status_code in _TRANSIENT_STATUS:
                    slow = False
                    last_error = f"http {r.status_code}"
                elif r.status_code >= 400:
                    raise self._refusal(r)
                else:
                    return self._ok(r)
            if attempt < self._retries:
                await self._sleep(0.2 + self._jitter() * 0.6)

        return await self._fallback("mind_slow" if slow else "mind_down", body, last_error)

    def _ok(self, r: httpx.Response) -> MindResult:
        raw = r.headers.get("x-mind-switched-off")
        if raw is not None:  # absent = an older Mind: do NOT read it as "nothing is off"
            self._set_off([] if raw.strip() == "none" else [m for m in raw.split(",") if m])
        return MindResult(
            response=r.json(),
            served_model=r.headers.get("x-mind-model"),
            switched_off=list(self._off),
        )

    @staticmethod
    def _refusal(r: httpx.Response) -> MindRefusedError:
        try:
            detail = str(r.json().get("detail", ""))
        except Exception:
            detail = ""
        return MindRefusedError(r.status_code, r.headers.get("x-mind-error"), detail, dict(r.headers))

    async def _fallback(self, reason: str, body: dict[str, Any], last_error: str) -> MindResult:
        # The floors never serve the caller's NAMED premium model: the standby gets
        # its own configured floor model, so a switched-off or unknown premium pick
        # can never be sent around Mind.
        if self._table_usable():
            for fl in self.table["floors"]:  # type: ignore[index]
                try:
                    r = await self._http.post(
                        f"{fl['url'].rstrip('/')}/v1/chat",
                        json={**body, "model": fl["model"]},
                        headers=self._headers(),
                        timeout=self._timeout,
                    )
                except (httpx.TimeoutException, httpx.TransportError):
                    continue
                if r.status_code < 400:
                    res = self._ok(r)
                    res.fallback, res.floor = reason, "standby"
                    return res
        if self._local_floor is not None:
            try:
                resp = await self._local_floor(body)
            except Exception as e:
                raise MindUnavailableError(reason, f"{last_error}; local floor failed: {type(e).__name__}") from e
            return MindResult(
                response=resp, served_model=resp.get("model"), fallback=reason, floor="local",
                switched_off=self.switched_off() or [],
            )
        raise MindUnavailableError(reason, last_error)
