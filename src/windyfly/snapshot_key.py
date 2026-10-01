"""Hub-held snapshot key fetch (AGENTS_CONTRACT_DRAFT §10d). OFF by default.

POST {hub}/api/v1/agents/{agent_id}/snapshot-key  {purpose: "sleep"|"wake"}
  Bearer = the agent's own EPT  ->  {key_b64, key_version, expires_in}

The key lives in memory only: never logged, never written to disk, and never
part of a snapshot. Gated by WINDY_SNAPSHOT_KEY=1 until the Hub route exists
(the Hub does not verify EPTs yet) and Grant approves the wording.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field

import httpx

from windyfly.hub_login import hub_url

PURPOSES = ("sleep", "wake")
TIMEOUT_S = 20.0


class SnapshotKeyError(RuntimeError):
    """The key could not be obtained. The message never contains the key."""


@dataclass(frozen=True)
class SnapshotKey:
    key: bytes = field(repr=False)
    version: int
    expires_in: int


def enabled() -> bool:
    return os.environ.get("WINDY_SNAPSHOT_KEY", "") == "1"


def fetch_snapshot_key(agent_id: str, purpose: str, ept: str,
                       *, client: httpx.Client | None = None) -> SnapshotKey:
    if not enabled():
        raise SnapshotKeyError("snapshot key disabled (WINDY_SNAPSHOT_KEY!=1)")
    if purpose not in PURPOSES:
        raise SnapshotKeyError(f"bad purpose {purpose!r}")
    if not ept:
        raise SnapshotKeyError("no EPT: rotate credentials first")
    url = f"{hub_url()}/api/v1/agents/{agent_id}/snapshot-key"
    own = client is None
    http = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        resp = http.post(url, json={"purpose": purpose},
                         headers={"Authorization": f"Bearer {ept}"})
    except httpx.HTTPError as e:
        raise SnapshotKeyError(f"hub unreachable: {type(e).__name__}") from e
    finally:
        if own:
            http.close()
    if resp.status_code == 403:
        raise SnapshotKeyError("hub refused the key (revoked or not allowed)")
    if resp.status_code != 200:
        raise SnapshotKeyError(f"hub answered {resp.status_code}")
    try:
        body = resp.json()
        key = base64.b64decode(body["key_b64"], validate=True)
        version = int(body["key_version"])
        expires = int(body.get("expires_in", 0))
    except (ValueError, KeyError, TypeError) as e:
        raise SnapshotKeyError("malformed key response") from e
    if len(key) != 32:
        raise SnapshotKeyError("key is not 32 bytes")
    return SnapshotKey(key=key, version=version, expires_in=expires)
