"""
Phase A M1 spike — single-tenant agent runtime in AWS Lambda.

Proves the wire protocol: Lambda can host an agent loop, with per-user
SQLite state on S3, with sub-second cold-start latency, and respond to
chat messages from the SPA via the existing /api/v1/fly/chat shape.

This is intentionally NOT a production agent. It's a slim proof-of-
concept that validates:
1. Lambda can package + cold-start the agent loop in <1s
2. S3 + SQLite round-trip is fast enough
3. Anthropic API calls work from inside Lambda
4. The wire protocol matches what windy-pro/account-server/src/routes/fly.ts
   already sends (POST /api/chat with {message, user_id})

If the spike succeeds, M2 swaps in real windy-agent code (memory, personality,
skills, channels). M2 is bigger; M1 is just the architectural validation.

Cross-reference: ~/kit-army-config/docs/phase-a-cloud-runtime-plan-2026-05-08.md
"""
import json
import os
import sqlite3
import time
import urllib.request
import urllib.error
import boto3
from typing import Any

# ─── Config ─────────────────────────────────────────────────────────
S3_BUCKET = os.environ.get("AGENT_STATE_BUCKET", "windyfly-cloud-runtime-state-dev")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DEFAULT_MODEL = os.environ.get("WINDYFLY_MODEL", "claude-sonnet-4-6")
MAX_HISTORY = 20  # turns kept in context

s3 = boto3.client("s3")


def _state_key(user_id: str) -> str:
    """S3 key for a user's SQLite state file."""
    return f"users/{user_id}/state.db"


def _local_state_path(user_id: str) -> str:
    """Lambda /tmp path for the user's state file during this invocation."""
    return f"/tmp/{user_id}_state.db"


def _load_state(user_id: str) -> str:
    """Pull state.db from S3 → /tmp. Returns local path. Creates if missing."""
    local = _local_state_path(user_id)
    try:
        s3.download_file(S3_BUCKET, _state_key(user_id), local)
    except s3.exceptions.NoSuchKey:
        # First invocation for this user — initialize a fresh DB.
        _init_fresh_db(local)
    except Exception as err:
        # Other S3 errors (e.g., 403, transient): also init fresh and log.
        # The next successful upload will heal the bucket state.
        print(f"[s3] download failed ({type(err).__name__}: {err}); initializing fresh DB")
        _init_fresh_db(local)
    return local


def _init_fresh_db(path: str) -> None:
    """Create the minimal schema for the spike."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
    """)
    conn.commit()
    conn.close()


def _persist_state(user_id: str) -> None:
    """Push /tmp state.db back to S3."""
    local = _local_state_path(user_id)
    s3.upload_file(local, S3_BUCKET, _state_key(user_id))


def _load_history(local_db: str) -> list[dict[str, str]]:
    """Recent N turns from this user's SQLite, in Anthropic message format."""
    conn = sqlite3.connect(local_db)
    cur = conn.execute(
        "SELECT role, content FROM messages "
        "ORDER BY created_at DESC LIMIT ?",
        (MAX_HISTORY,),
    )
    rows = cur.fetchall()
    conn.close()
    # Reverse so chronological order goes into the prompt.
    return [{"role": r, "content": c} for r, c in reversed(rows)]


def _append_message(local_db: str, role: str, content: str) -> None:
    """Add a new message to the user's history."""
    conn = sqlite3.connect(local_db)
    conn.execute(
        "INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)",
        (role, content, time.time()),
    )
    conn.commit()
    conn.close()


def _call_anthropic(messages: list[dict[str, str]], model: str) -> str:
    """
    Direct HTTPS call to Anthropic API. Avoids the anthropic SDK in the spike
    so we don't bloat the Lambda package — slim deps = fast cold start.

    Supports BOTH auth methods:
    - sk-ant-api03-* (regular API key) → x-api-key header, plain system prompt
    - sk-ant-oat01-* (OAuth token from Claude Pro/Max subscription) → Bearer
      auth + 'anthropic-beta: oauth-2025-04-20' + system-prompt content-block
      array where the FIRST block is exactly 'You are Claude Code, Anthropic's
      official CLI for Claude.' (per Anthropic's OAuth gate; see memory
      reference_anthropic_oauth_gate.md). The second block is our real
      Windy Fly system prompt.

    OAuth path lets the spike pull from Grant's $200/mo Max subscription
    quota instead of pay-as-you-go credits.
    """
    if not ANTHROPIC_API_KEY:
        return "[spike] ANTHROPIC_API_KEY unset — agent would respond here"

    is_oauth = ANTHROPIC_API_KEY.startswith("sk-ant-oat01")
    fly_system_prompt = (
        "You are Windy Fly — a personal AI agent in the Windy ecosystem. "
        "Be concise, helpful, and friendly. This is the Phase A M1 spike, "
        "running in AWS Lambda with state persisted to S3."
    )

    if is_oauth:
        # OAuth gate: system MUST be a content-block array; first block exact.
        body = {
            "model": model,
            "max_tokens": 1024,
            "system": [
                {"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."},
                {"type": "text", "text": fly_system_prompt},
            ],
            "messages": messages,
        }
        headers = {
            "Authorization": f"Bearer {ANTHROPIC_API_KEY}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20",
            "content-type": "application/json",
        }
    else:
        # Regular API key path.
        body = {
            "model": model,
            "max_tokens": 1024,
            "system": fly_system_prompt,
            "messages": messages,
        }
        headers = {
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            content_blocks = body.get("content", [])
            text_parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
            return "".join(text_parts).strip() or "[spike] empty response from Anthropic"
    except urllib.error.HTTPError as err:
        return f"[spike] Anthropic API error: HTTP {err.code} — {err.reason}"
    except urllib.error.URLError as err:
        return f"[spike] Anthropic API unreachable: {err.reason}"
    except Exception as err:  # noqa: BLE001
        return f"[spike] unexpected error: {type(err).__name__}: {err}"


# ─── Lambda entry point ─────────────────────────────────────────────

def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Event shape (matches what account-server/src/routes/fly.ts already sends
    via POST {agentUrl}/api/chat with body {message, user_id}):

        {
          "message": "Hello, agent",
          "user_id": "ET26-XXXX-XXXX"  // Eternitas passport or windy_identity_id
        }

    Returns:
        {
          "response": "agent's reply",
          "cold_start_ms": <bool>,  // true if this was a cold-start invocation
          "total_ms": <int>,
          "s3_load_ms": <int>,
          "llm_ms": <int>,
          "s3_persist_ms": <int>
        }
    """
    t_start = time.time()
    cold_start = not getattr(lambda_handler, "_warm", False)
    lambda_handler._warm = True  # type: ignore[attr-defined]

    # API Gateway HTTP API wraps the body — unwrap if needed.
    body = event
    if isinstance(event, dict) and "body" in event and isinstance(event["body"], str):
        try:
            body = json.loads(event["body"])
        except json.JSONDecodeError:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Invalid JSON body"}),
            }

    message = (body.get("message") or "").strip()
    user_id = (body.get("user_id") or "").strip()

    if not message or not user_id:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "message and user_id are required"}),
        }

    # 1. Pull state.db from S3 (or init fresh)
    t_s3_load_start = time.time()
    local_db = _load_state(user_id)
    s3_load_ms = int((time.time() - t_s3_load_start) * 1000)

    # 2. Append user message + load history for prompt
    _append_message(local_db, "user", message)
    history = _load_history(local_db)

    # 3. Call LLM
    t_llm_start = time.time()
    response_text = _call_anthropic(history, DEFAULT_MODEL)
    llm_ms = int((time.time() - t_llm_start) * 1000)

    # 4. Append assistant response
    _append_message(local_db, "assistant", response_text)

    # 5. Persist state back to S3
    t_s3_persist_start = time.time()
    _persist_state(user_id)
    s3_persist_ms = int((time.time() - t_s3_persist_start) * 1000)

    total_ms = int((time.time() - t_start) * 1000)

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "response": response_text,
            "cold_start": cold_start,
            "timing_ms": {
                "total": total_ms,
                "s3_load": s3_load_ms,
                "llm": llm_ms,
                "s3_persist": s3_persist_ms,
            },
        }),
    }
