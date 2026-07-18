#!/usr/bin/env python3
"""Daily Journal writer (Chronicle Doctrine Build 3).

Writes yesterday's Journal entries (idle-gap chapters) over the raw
Chronicle. Idempotent (upsert), best-effort, degrades to a
deterministic extractive skeleton when no model is reachable. Run from
a systemd timer just after midnight. Optional arg: a 'YYYY-MM-DD' date
to (re)write a specific day; default = yesterday (UTC).

Usage:
  write-journal.py            # yesterday
  write-journal.py 2026-07-17 # a specific day (backfill/repair)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

_AGENT_SRC = os.environ.get("_AGENT_SRC")
if _AGENT_SRC and _AGENT_SRC not in sys.path:
    sys.path.insert(0, _AGENT_SRC)


def _model_caller(cfg):
    """Wrap call_llm into the (messages, *, max_tokens) -> str shape the
    Journal enricher expects. Returns None if the model layer can't be
    imported, so the writer degrades to the extractive skeleton."""
    try:
        from windyfly.agent.models import call_llm
    except Exception:
        return None

    def _call(messages, *, max_tokens=700):
        model = (cfg.get("agent", {}) or {}).get(
            "journal_model", "claude-haiku-4-5"
        )
        res = call_llm(
            messages, model=model, max_tokens=max_tokens,
            temperature=0.3, config=cfg,
        )
        return res.get("content", "") if isinstance(res, dict) else ""

    return _call


def main() -> int:
    day = sys.argv[1] if len(sys.argv) > 1 else (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).strftime("%Y-%m-%d")

    from windyfly.config import load_config
    from windyfly.memory.database import Database
    from windyfly.memory.journal import write_day

    config_path = os.environ.get(
        "WINDYFLY_CONFIG",
        os.path.expanduser("~/.local/share/windyfly/soul/config.toml"),
    )
    cfg = load_config(config_path)
    db_path = os.environ.get(
        "WINDYFLY_DB_PATH",
        (cfg.get("memory", {}) or {}).get("db_path", "data/windyfly.db"),
    )
    db = Database(db_path)

    n = write_day(db, day, model_caller=_model_caller(cfg))
    print(f"journal: {n} entr{'y' if n == 1 else 'ies'} written for {day}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
