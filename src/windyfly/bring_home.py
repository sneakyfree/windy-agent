"""``windy bring-home`` — move the owner's cloud-born agent onto this machine.

The hub handover (windy-pro #621; spec AGENT_HANDOVER.md §9):

1. ONE fresh browser sign-in (``prompt=login``): the hub and Eternitas both
   want ``auth_time`` ≤ 600 s to move live credentials.
2. The agent's own ES256 key is generated here and registered at Eternitas
   on the owner path (``reason: handover``). Old keys are left alone: the
   cloud body's key is its platform's to revoke when it stands down, and a
   handover that fails must leave the cloud agent working.
3. ``POST /api/v1/agent/{passport}/handover`` → 202, then the one-time
   credentials pickup (428 while chat/mail prepare, 200 ONCE, then 410).
4. The pickup body is sealed to a 0600 file (written, fsync'd, renamed) the
   moment it arrives, then installed piece by piece. A crash after the 200
   loses nothing: the next run resumes from the sealed file. A crash before
   it → run again with the same key = a rotate round (fresh credentials).
5. Memory: a one-time download (``ndjson-v1``, gzip + base64, sha256 over
   the NDJSON). It is kept verbatim, imported as episodes, and the turnover
   letter becomes the first thing the local agent reads.
6. The EPT comes from Eternitas via the new key's proof (no bearer), with the
   owner's fresh sign-in as the fallback. The hub never carries an EPT.

Nothing here prints a token.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import os
import socket
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

POLL_S = 3.0
HTTP_TIMEOUT_S = 30.0
MEMORY_FORMAT = "ndjson-v1"


# ── small helpers ────────────────────────────────────────────────────

def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _code(resp: httpx.Response) -> str:
    data = _json(resp)
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("code") or "")
    detail = data.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("code") or "")
    return str(err or data.get("code") or "")


def _state_dir() -> Path:
    from windyfly.hub_hatch import _state_dir as hub_state_dir

    return hub_state_dir()


def _seal_path(passport: str) -> Path:
    return _state_dir() / f"handover-{passport}.json"


def _atomic_write(path: Path, data: bytes) -> None:
    """0600 temp file in the same directory, fsync, rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _shred(path: Path) -> None:
    try:
        size = path.stat().st_size
        with open(path, "r+b") as fh:
            fh.write(os.urandom(size))
            fh.flush()
            os.fsync(fh.fileno())
        path.unlink()
    except OSError:
        pass


def env_file_path() -> Path:
    """The file the runtime loads: WINDY_ENV_FILE, else the project .env."""
    explicit = os.environ.get("WINDY_ENV_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    from windyfly.platform import get_project_root

    return get_project_root() / ".env"


def upsert_env(values: dict[str, str], path: Path | None = None) -> Path:
    """Set several vars in the env file at once, atomically (0600)."""
    path = path or env_file_path()
    lines: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    pending = dict(values)
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line and not line.lstrip().startswith("#") else ""
        if key in pending:
            out.append(f"{key}={pending.pop(key)}")
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in pending.items())
    _atomic_write(path, ("\n".join(out) + "\n").encode("utf-8"))
    for k, v in values.items():
        os.environ[k] = v
    return path


def runtime_label() -> str:
    try:
        name = socket.gethostname()
    except OSError:
        name = ""
    return (name or "this machine")[:60]


def idempotency_key(passport: str) -> str:
    """Stable per install + passport: a retry is the same round, and a retry
    after a delivered pickup is a rotate round (fresh credentials)."""
    from windyfly.hub_hatch import _install_state

    return f"windyfly-home-{_install_state()['install_id']}-{passport}"[:128]


def _deadline(expires_at: Any, now: float, fallback_s: float = 15 * 60) -> float:
    if isinstance(expires_at, str) and expires_at:
        try:
            return datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return now + fallback_s


# ── the hub calls ────────────────────────────────────────────────────

def start_handover(passport: str, token: str, *, key: str,
                   transport: httpx.BaseTransport | None = None) -> httpx.Response:
    from windyfly.hub_login import hub_url

    with httpx.Client(timeout=HTTP_TIMEOUT_S, transport=transport) as client:
        return client.post(
            f"{hub_url()}/api/v1/agent/{passport}/handover",
            json={"to": "external", "runtime_label": runtime_label()},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                     "Idempotency-Key": key},
        )


def wait_for_pickup(url: str, token: str, *, expires_at: Any = None,
                    transport: httpx.BaseTransport | None = None,
                    sleep: Callable[[float], None] = time.sleep,
                    now: Callable[[], float] = time.time,
                    on_wait: Callable[[list[str]], None] | None = None,
                    ) -> tuple[str, dict[str, Any], httpx.Response | None]:
    """GET the pickup until 200 (once). Returns (status, body, last response):
    ``ready`` | ``gone`` (410) | ``timeout`` | ``failed``."""
    deadline = _deadline(expires_at, now())
    last: httpx.Response | None = None
    seen: list[str] | None = None
    with httpx.Client(timeout=HTTP_TIMEOUT_S, transport=transport) as client:
        while True:
            try:
                last = client.get(url, headers={"Authorization": f"Bearer {token}",
                                                "Accept": "application/json"})
            except httpx.HTTPError as exc:
                logger.warning("bring-home: pickup poll error (%s); retrying", type(exc).__name__)
                last = None
            if last is not None:
                if last.status_code == 200:
                    return "ready", _json(last), last
                if last.status_code == 410:
                    return "gone", _json(last), last
                if last.status_code == 428:
                    waiting = _json(last).get("waiting_for")
                    waiting = [str(w) for w in waiting] if isinstance(waiting, list) else []
                    if on_wait and waiting != seen:
                        on_wait(waiting)
                    seen = waiting
                elif last.status_code < 500 and last.status_code != 429:
                    return "failed", _json(last), last
            if now() >= deadline:
                return "timeout", {}, last
            sleep(POLL_S)


def fetch_memory(url: str, token: str, *,
                 transport: httpx.BaseTransport | None = None) -> tuple[int, dict[str, Any]]:
    with httpx.Client(timeout=120.0, transport=transport) as client:
        resp = client.get(url, headers={"Authorization": f"Bearer {token}",
                                        "Accept": "application/json"})
    return resp.status_code, _json(resp)


# ── installing the pickup ────────────────────────────────────────────

def ensure_brain(agent_name: str) -> None:
    """A cloud-born agent had no local config: give the body brought home the
    keyless Windy Mind brain `windy go --keyless` writes (authenticated by its
    own passport token), BEFORE the handed-over credentials are installed
    (writing that config rewrites the project .env)."""
    from windyfly.agent.models import MIND_DEFAULT_URL, MIND_URL_ENV
    from windyfly.quickstart import KEYLESS_MODEL, is_keyless_configured, write_keyless_config

    if not os.environ.get("WINDY_ENV_FILE", "").strip() and not is_keyless_configured():
        if agent_name:
            os.environ["WINDYFLY_AGENT_NAME"] = agent_name
        write_keyless_config()
    path = env_file_path()
    try:
        have = {ln.split("=", 1)[0] for ln in path.read_text(encoding="utf-8").splitlines() if "=" in ln}
    except OSError:
        have = set()
    brain = {MIND_URL_ENV: os.environ.get(MIND_URL_ENV) or MIND_DEFAULT_URL, "WINDY_MIND_SEND_TOOLS": "1"}
    if "DEFAULT_MODEL" not in have:
        brain["DEFAULT_MODEL"] = KEYLESS_MODEL
    upsert_env({k: v for k, v in brain.items() if k not in have or k == "WINDY_MIND_SEND_TOOLS"})


def install_credentials(body: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    """Put the pickup's Matrix + mail credentials where the runtime reads them."""
    passport = str(body.get("passport_number") or rec.get("passport_number") or "")
    matrix = _dict(body.get("matrix"))
    mail = _dict(body.get("mail"))
    env: dict[str, str] = {"ETERNITAS_PASSPORT": passport}
    if matrix.get("access_token"):
        env["MATRIX_BOT_TOKEN"] = str(matrix["access_token"])
    if matrix.get("device_id"):
        env["MATRIX_DEVICE_ID"] = str(matrix["device_id"])
    if matrix.get("homeserver"):
        env["MATRIX_HOMESERVER"] = str(matrix["homeserver"])
    user = matrix.get("user_id") or matrix.get("matrix_user_id") or rec.get("matrix_user_id")
    if user:
        env["MATRIX_BOT_USER"] = str(user)
    if rec.get("dm_room_id"):
        env["MATRIX_DM_ROOM_ID"] = str(rec["dm_room_id"])
    if mail.get("address"):
        env["WINDYMAIL_EMAIL"] = str(mail["address"])
    env_path = upsert_env(env)

    if mail:
        from windyfly.eternitas import agent_keys

        data = agent_keys.load_credentials()
        data["windy_mail"] = {k: mail[k] for k in mail}
        agent_keys.save_credentials(data)
    return {"env_file": str(env_path), "matrix": bool(matrix.get("access_token")),
            "mail": bool(mail)}


def decode_memory(doc: dict[str, Any]) -> bytes:
    """``ndjson-v1``: base64 of gzip of the NDJSON (plain base64 tolerated)."""
    raw = base64.b64decode(str(doc.get("inline") or ""))
    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


def _line_role(ev: dict[str, Any]) -> str:
    role = str(ev.get("role") or ev.get("sender_role") or ev.get("kind") or "").lower()
    if role in ("user", "human", "owner"):
        return "user"
    if role in ("assistant", "agent", "bot", "fly"):
        return "assistant"
    if role == "system":
        return "system"
    return ""


def _line_text(ev: dict[str, Any]) -> str:
    for k in ("content", "text", "body", "message"):
        v = ev.get(k)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, dict) and isinstance(v.get("body"), str):
            return v["body"]
    return ""


def import_memory(ndjson: bytes, *, handover_id: str, db: Any) -> dict[str, int]:
    """One episode per mappable line (role + text). Unmappable lines are
    counted, not guessed; the verbatim NDJSON is kept beside the state."""
    from windyfly.memory.episodes import save_episode

    imported = skipped = 0
    for line in ndjson.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if not isinstance(ev, dict):
            skipped += 1
            continue
        role, text = _line_role(ev), _line_text(ev)
        if not role or not text:
            skipped += 1
            continue
        save_episode(db, role, text, session_id=f"handover:{handover_id}")
        imported += 1
    return {"imported": imported, "skipped": skipped}


def write_turnover(letter: str, passport: str, db: Any) -> None:
    from windyfly.memory.nodes import upsert_node

    upsert_node(
        db,
        type="turnover_letter",
        name=f"turnover:handover:{passport}",
        metadata={"summary": letter[:4000], "source": "hub_handover",
                  "written_at": datetime.now(timezone.utc).isoformat()},
        epistemic_status="verified",
        confidence=1.0,
        source="handover",
    )


def _open_db() -> Any:
    from windyfly.memory.database import Database

    return Database(os.environ.get("WINDYFLY_DB_PATH", "data/windyfly.db"))


def install_memory(meta: dict[str, Any], passport: str, handover_id: str, token: str, *,
                   transport: httpx.BaseTransport | None = None,
                   db_factory: Callable[[], Any] = _open_db) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "none"}
    letter = str(meta.get("turnover_letter") or "")
    url = str(meta.get("url") or "")
    if url:
        code, doc = fetch_memory(url, token, transport=transport)
        if code != 200:
            out = {"status": "download_failed", "http": code}
        elif str(doc.get("format") or meta.get("format")) != MEMORY_FORMAT:
            # Unknown shape: keep it untouched and say so; never guess.
            _atomic_write(_state_dir() / f"memory-{handover_id}.raw.json",
                          json.dumps(doc).encode("utf-8"))
            out = {"status": "unknown_format", "format": doc.get("format"),
                   "keys": sorted(doc)}
        else:
            ndjson = decode_memory(doc)
            want_sha = str(doc.get("sha256") or meta.get("sha256") or "")
            got_sha = hashlib.sha256(ndjson).hexdigest()
            lines = [ln for ln in ndjson.splitlines() if ln.strip()]
            want_n = doc.get("event_count", meta.get("event_count"))
            _atomic_write(_state_dir() / f"memory-{handover_id}.ndjson", ndjson)
            if want_sha and got_sha != want_sha:
                out = {"status": "sha_mismatch"}
            elif isinstance(want_n, int) and want_n != len(lines):
                out = {"status": "count_mismatch", "expected": want_n, "got": len(lines)}
            else:
                db = db_factory()
                try:
                    out = {"status": "imported", **import_memory(ndjson, handover_id=handover_id, db=db)}
                    if letter:
                        write_turnover(letter, passport, db)
                finally:
                    close = getattr(db, "close", None)
                    if callable(close):
                        close()
    if letter and out.get("status") != "imported":
        db = db_factory()
        try:
            write_turnover(letter, passport, db)
        finally:
            close = getattr(db, "close", None)
            if callable(close):
                close()
    out["turnover_letter"] = bool(letter)
    return out


def get_ept(passport: str, owner_token: str, *,
            transport: httpx.BaseTransport | None = None) -> str:
    """Key proof first (no bearer); the owner's fresh sign-in as fallback."""
    from windyfly.eternitas import ept_refresh

    os.environ["ETERNITAS_PASSPORT"] = passport
    res = ept_refresh.refresh_ept(force=True, transport=transport)
    token = os.environ.get("ETERNITAS_PASSPORT_TOKEN", "")
    if res.get("status") in ("refreshed", "unchanged") and token:
        return token
    from windyfly.eternitas.url import resolve_eternitas_url

    url = f"{resolve_eternitas_url('https://api.eternitas.ai')}/api/v1/bots/{passport}/ept/refresh"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_S, transport=transport) as client:
            resp = client.post(url, json={}, headers={"Authorization": f"Bearer {owner_token}"})
    except httpx.HTTPError:
        return ""
    if resp.status_code != 200:
        return ""
    token = str(_json(resp).get("ept_token") or "")
    if token:
        upsert_env({"ETERNITAS_PASSPORT_TOKEN": token})
    return token


# ── the command ──────────────────────────────────────────────────────

def run(
    console: Any,
    *,
    owner_token: Callable[[], str] | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    db_factory: Callable[[], Any] = _open_db,
) -> int:
    from windyfly import hub_hatch
    from windyfly.eternitas import agent_keys

    rec = hub_hatch.cloud_agent()
    if not rec:
        console.print("  No cloud agent on this machine; run [bold]windy go[/bold] first.")
        return 1
    passport = rec["passport_number"]
    name = rec.get("agent_name") or "Your agent"
    if rec.get("where") == "home":
        console.print(f"  {name} ({passport}) already lives on this machine. "
                      "Start it with [bold]windy start[/bold].")
        return 0

    get_token = owner_token or (lambda: agent_keys.fresh_owner_token(open_browser=True))
    sealed = _seal_path(passport)
    body: dict[str, Any] = {}
    kid = ""
    token = ""
    if sealed.exists():
        # A pickup that arrived before a crash: install it, no network needed.
        try:
            body = json.loads(sealed.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            body = {}
        if body:
            console.print("  Resuming the handover that was already picked up…")

    if not body:
        console.print(f"  Bringing [bold]{name}[/bold] ({passport}) home. "
                      "Sign in once more to confirm it's you…")
        try:
            token = str(get_token() or "")
        except Exception as exc:
            console.print(f"  [red]Sign-in failed:[/red] {exc}")
            return 1
        if not token:
            console.print("  [red]Sign-in didn't complete.[/red] Nothing was changed.")
            return 1

        reg = agent_keys.reset(passport=passport, owner_token=lambda: token,
                               reason="handover", revoke_old=False, transport=transport)
        if reg.get("status") == "reset":
            kid = str(reg.get("kid") or "")
        else:
            console.print(f"  [yellow]Couldn't register this machine's key at Eternitas "
                          f"({reg.get('code') or reg.get('status')}); continuing — the passport "
                          "token will come from your sign-in instead.[/yellow]")

        def rollback(why: str) -> int:
            if kid:
                agent_keys.revoke(kid, reason=f"bring-home failed: {why}",
                                  passport=passport, bearer=token, transport=transport)
            return 1

        key = idempotency_key(passport)
        resp = start_handover(passport, token, key=key, transport=transport)
        if resp.status_code == 401 and _code(resp) == "reauth_required":
            token = str(get_token() or "")
            resp = start_handover(passport, token, key=key, transport=transport) if token else resp
        if resp.status_code == 409 and _code(resp) == "already_external":
            console.print(f"  {name} already lives outside the cloud (brought home elsewhere). "
                          "Nothing was changed here.")
            return rollback("already_external")
        if resp.status_code == 503:
            console.print("  The hub can't hand agents over right now (handover_unavailable). "
                          f"{name} stays in the cloud; try again later.")
            return rollback("handover_unavailable")
        if resp.status_code not in (200, 202):
            console.print(f"  [red]The hub refused the handover[/red] "
                          f"(HTTP {resp.status_code} {_code(resp)}). {name} stays in the cloud.")
            return rollback(f"http_{resp.status_code}")
        started = _json(resp)
        pickup = _dict(started.get("credentials_pickup"))
        if not pickup.get("url"):
            console.print("  [red]The hub started the handover but sent no pickup link.[/red]")
            return rollback("no_pickup")

        console.print("  Waiting for Windy Chat and Windy Mail to hand over…")
        status, body, last = wait_for_pickup(
            str(pickup["url"]), token, expires_at=pickup.get("expires_at"),
            transport=transport, sleep=sleep, now=now,
            on_wait=lambda w: console.print(f"  [dim]…waiting for {', '.join(w) or 'the hub'}[/dim]"),
        )
        if status == "gone":
            console.print("  The pickup was already collected or has expired. Run "
                          "[bold]windy bring-home[/bold] again: the hub issues fresh credentials.")
            return 1
        if status != "ready":
            code = last.status_code if last is not None else "no answer"
            console.print(f"  [red]The handover didn't finish[/red] ({status}, {code}). "
                          f"If it never completes, the hub gives {name} back to the cloud.")
            return 1
        # Seal it the moment it arrives: from here a crash loses nothing.
        _atomic_write(sealed, json.dumps(body).encode("utf-8"))

    ensure_brain(str(rec.get("agent_name") or ""))
    installed = install_credentials(body, rec)
    memory = _dict(body.get("memory"))
    if not token and memory.get("url"):
        # Resumed after a crash: the one-time memory download needs the owner.
        try:
            token = str(get_token() or "")
        except Exception:
            token = ""
    handover_id = str(body.get("handover_id") or "")
    mem: dict[str, Any] = {"status": "skipped"}
    if memory and token:
        mem = install_memory(memory, passport, handover_id, token,
                             transport=transport, db_factory=db_factory)
    elif memory.get("turnover_letter"):
        mem = install_memory({"turnover_letter": memory["turnover_letter"]}, passport,
                             handover_id, "", transport=transport, db_factory=db_factory)

    ept = get_ept(passport, token, transport=transport)

    rec.update({"where": "home", "home_since": int(now()), "handover_id": handover_id})
    hub_hatch._write_private_json(hub_hatch.cloud_agent_path(), rec)
    _shred(sealed)

    console.print(f"\n  [bold green]{name} is home.[/bold green] "
                  f"Say hi in Windy Chat: {rec.get('chat_url') or rec.get('matrix_user_id') or ''}")
    if mem.get("status") == "imported":
        console.print(f"  Memory: {mem.get('imported', 0)} messages carried over"
                      + (f" ({mem['skipped']} kept only in the raw archive)" if mem.get("skipped") else "")
                      + (", plus its turnover letter." if mem.get("turnover_letter") else "."))
    elif mem.get("status") not in ("skipped", "none"):
        console.print(f"  [yellow]Memory wasn't imported ({mem.get('status')}); the raw download "
                      "is kept beside your Windy sign-in.[/yellow]")
    if not ept:
        console.print("  [yellow]No passport token yet — run [bold]windy ept refresh[/bold].[/yellow]")
    if not installed.get("matrix"):
        console.print("  [yellow]The pickup had no Windy Chat credentials.[/yellow]")
    where = Path(installed.get("env_file") or "").parent
    console.print(f"  Its settings are in {where}. Start it from there with [bold]windy start[/bold].")
    return 0
