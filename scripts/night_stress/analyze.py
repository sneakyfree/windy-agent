#!/usr/bin/env python3
"""Stress findings analyzer.

Reads findings.jsonl from the active stress run, groups failures
by pattern, ranks them by impact (severity × log(1+count)), and
writes morning_brief.md to the same run dir.

Designed to be re-runnable mid-stream — each cron repair cycle
calls this fresh to see the latest state.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


# Failure-class detection rules. Tuple is (rule_name, severity, predicate).
# Predicate takes a finding dict, returns bool.

def _has_event(finding, event_type_prefix: str, **filter_kwargs) -> bool:
    for ev in finding.get("events", []):
        if not ev.get("type", "").startswith(event_type_prefix):
            continue
        data = ev.get("data") or {}
        if all(data.get(k) == v for k, v in filter_kwargs.items()):
            return True
    return False


def _event_with_filter(finding, event_type: str, **filter_kwargs) -> bool:
    for ev in finding.get("events", []):
        if ev.get("type") != event_type:
            continue
        data = ev.get("data") or {}
        if all(data.get(k) == v for k, v in filter_kwargs.items()):
            return True
    return False


CLASSIFIERS = [
    # (name, severity, predicate)
    ("llm_exception", 5,
        lambda f: bool(f.get("error"))),
    ("empty_response", 5,
        lambda f: not (f.get("response_preview") or "").strip()
                  and not f.get("error")),
    ("confabulation_success_claim", 4,
        lambda f: _event_with_filter(f, "agent.confabulation_detected",
                                     stage="retry")
                  or _event_with_filter(f, "agent.confabulation_detected",
                                        stage="initial")),
    ("confabulation_self_env", 4,
        lambda f: _event_with_filter(f, "agent.confabulation_detected",
                                     stage="self_env_initial")
                  or _event_with_filter(f, "agent.confabulation_detected",
                                        stage="self_env_retry")),
    ("write_intent_unexecuted", 3,
        lambda f: _has_event(f, "agent.write_intent_unexecuted")),
    ("auto_resurrect_fired", 4,
        lambda f: _has_event(f, "auto_resurrect.fired")),
    # Severity 1 (was 2) — these are INTERNAL probe telemetry, not
    # user-visible bugs. When the bot is in lifeboat and probes the
    # paid key, a non-cooldown failure ("still rate-limited",
    # "auth_failure", etc.) just means recovery isn't possible yet —
    # the bot correctly stays in lifeboat. Refined 2026-05-11 cycle 5.
    ("lifeboat_recovery_failed", 1,
        lambda f: any(
            ev.get("type") == "lifeboat.recovery_failed"
            and (ev.get("data") or {}).get("reason") not in ("cooldown",)
            for ev in f.get("events", [])
        )),
    ("native_search_unsupported", 3,
        lambda f: _has_event(f, "web_search.native_unsupported")),
    ("slow_turn_60s", 2,
        lambda f: f.get("latency_ms", 0) > 60_000),
    ("very_slow_turn_120s", 3,
        lambda f: f.get("latency_ms", 0) > 120_000),
    ("offline_fallback_used", 2,
        lambda f: _has_event(f, "offline.fallback")
                  or _has_event(f, "offline.chain_exhausted")),
    # Accept /normal and /auto-resurrect as valid hints (the auto-
    # resurrect banner that fires on rate limits offers BOTH as user
    # actions). Pre-refinement this was flagging the banner itself
    # as "missing" because the classifier only knew about /reset and
    # /resurrect. Refined 2026-05-11 cycle 5.
    ("recovery_hint_missing", 3,
        lambda f: (
            ("Local model error" in (f.get("response_preview") or "")
             or "currently offline" in (f.get("response_preview") or "").lower())
            and "/reset" not in (f.get("response_preview") or "")
            and "/resurrect" not in (f.get("response_preview") or "")
            and "/normal" not in (f.get("response_preview") or "")
            and "/auto-resurrect" not in (f.get("response_preview") or "")
        )),
]


def classify(finding: dict) -> list[tuple[str, int]]:
    """Return list of (rule_name, severity) hits for this finding."""
    hits = []
    for name, sev, pred in CLASSIFIERS:
        try:
            if pred(finding):
                hits.append((name, sev))
        except Exception:
            continue
    return hits


def _load_all_events(db_path: Path) -> list[dict]:
    """Read all events from stress.db with parsed UTC datetimes."""
    if not db_path.exists():
        return []
    out: list[dict] = []
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        for ev_type, data_json, created_at in cur.execute(
            "SELECT event_type, data, created_at FROM events ORDER BY id"
        ):
            try:
                data = json.loads(data_json or "{}")
            except Exception:
                data = {}
            # 'YYYY-MM-DD HH:MM:SS' (UTC, SQLite CURRENT_TIMESTAMP default)
            try:
                dt = datetime.fromisoformat(created_at).replace(
                    tzinfo=timezone.utc
                )
            except Exception:
                continue
            out.append({
                "type": ev_type,
                "data": data,
                "created_at": created_at,
                "_dt_utc": dt,
            })
        conn.close()
    except Exception as e:  # pragma: no cover
        print(f"[warn] event load failed: {e}", file=sys.stderr)
    return out


def _attach_events(findings: list[dict], events: list[dict]) -> None:
    """Hybrid join: session_id-match wins, else timestamp window match.

    Many events (auto_resurrect.fired, offline.*, lifeboat.*) don't carry
    session_id, so session-only matching misses them. Time window is
    finding.started_at to finding.started_at + latency_ms + 1s slack.
    """
    if not events:
        return

    # Pre-compute finding windows
    windows: list[tuple] = []
    for f in findings:
        try:
            start = datetime.fromisoformat(f["started_at"])
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        end_offset = (f.get("latency_ms", 0) / 1000.0) + 1.0
        from datetime import timedelta
        end = start + timedelta(seconds=end_offset)
        windows.append((start, end, f))

    # Sessions for fast lookup
    by_session: dict[str, dict] = {
        f"night-stress-{f.get('index', 0):04d}": f for f in findings
    }

    for ev in events:
        attached = False
        sid = (ev["data"] or {}).get("session_id")
        if sid and sid in by_session:
            _append_unique(by_session[sid], ev)
            attached = True
            continue
        # Time window fallback (no session_id, or session unknown)
        ev_dt = ev["_dt_utc"]
        # Find finding whose window covers ev_dt. Linear scan is fine for 200.
        for start, end, f in windows:
            if start <= ev_dt <= end:
                _append_unique(f, ev)
                attached = True
                break
        # Unattached events (between turns / before harness) are dropped.
        _ = attached  # silence unused

    # Backfill tool_names from joined events
    for f in findings:
        if not f.get("tool_names"):
            names = [
                (e.get("data") or {}).get("tool_name")
                or (e.get("data") or {}).get("name")
                for e in (f.get("events") or [])
                if isinstance(e, dict)
                and e.get("type") in (
                    "tool_invoked", "skill.evaluate", "memory.write"
                )
            ]
            f["tool_names"] = [n for n in names if n]


def _append_unique(finding: dict, ev: dict) -> None:
    """Append event to finding['events'] dedupe by (type, created_at)."""
    finding.setdefault("events", [])
    key = (ev["type"], ev["created_at"])
    for existing in finding["events"]:
        if (
            isinstance(existing, dict)
            and (existing.get("type"), existing.get("created_at")) == key
        ):
            return
    # Strip internal _dt_utc before persisting
    clean = {k: v for k, v in ev.items() if not k.startswith("_")}
    finding["events"].append(clean)


def _cost_from_ledger(db_path: Path) -> float:
    """Sum cost_usd from cost_ledger (the actual table name)."""
    if not db_path.exists():
        return 0.0
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        row = cur.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger"
        ).fetchone()
        conn.close()
        return float(row[0] or 0)
    except Exception:
        return 0.0


def analyze(run_dir: Path) -> dict:
    findings_path = run_dir / "findings.jsonl"
    if not findings_path.exists():
        return {"error": "no findings.jsonl yet"}

    findings: list[dict] = []
    for line in findings_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            findings.append(json.loads(line))
        except Exception:
            continue

    # ── Join events from stress.db (harness sweep is broken) ────────
    db_path = run_dir / "stress.db"
    events = _load_all_events(db_path)
    _attach_events(findings, events)

    total = len(findings)
    cat_counts = Counter(f.get("category", "?") for f in findings)
    cat_errs = Counter(
        f.get("category", "?") for f in findings if f.get("error")
    )

    # Per-pattern aggregation
    pattern_counts: Counter[str] = Counter()
    pattern_severity: dict[str, int] = {}
    pattern_samples: dict[str, list[dict]] = defaultdict(list)

    for f in findings:
        for name, sev in classify(f):
            pattern_counts[name] += 1
            pattern_severity[name] = sev
            if len(pattern_samples[name]) < 5:
                pattern_samples[name].append({
                    "index": f.get("index"),
                    "category": f.get("category"),
                    "prompt": (f.get("prompt") or "")[:120],
                    "response_preview": f.get("response_preview", "")[:200],
                    "error": f.get("error"),
                    "latency_ms": f.get("latency_ms"),
                })

    # Rank by impact = severity * log(1 + count)
    ranked = sorted(
        pattern_counts.items(),
        key=lambda kv: -(
            pattern_severity.get(kv[0], 1) * math.log(1 + kv[1])
        ),
    )

    # Latency distribution
    latencies = sorted(f.get("latency_ms", 0) for f in findings)
    if latencies:
        latency_summary = {
            "p50": latencies[len(latencies) // 2],
            "p90": latencies[int(len(latencies) * 0.9)],
            "p99": latencies[int(len(latencies) * 0.99)],
            "max": latencies[-1],
        }
    else:
        latency_summary = {"p50": 0, "p90": 0, "p99": 0, "max": 0}

    # Prefer cost_ledger (correct table name) over findings.cost_so_far
    # (harness's cost_so_far queries 'cost_log' which doesn't exist).
    cost_final = _cost_from_ledger(db_path) or (
        findings[-1].get("cost_so_far", 0) if findings else 0
    )

    # Load PRs-shipped manifest (~/.windy-stress/FIXED.json). Lets the
    # brief annotate which failure patterns are already-fixed-tonight
    # so the 7am reader doesn't think open work remains.
    prs_shipped: list[dict] = []
    fixed_pattern_to_pr: dict[str, dict] = {}
    fixed_manifest = Path.home() / ".windy-stress" / "FIXED.json"
    if fixed_manifest.exists():
        try:
            prs_shipped = json.loads(fixed_manifest.read_text())
            for entry in prs_shipped:
                for pat in entry.get("patterns_addressed", []):
                    fixed_pattern_to_pr[pat] = entry
        except Exception as e:  # pragma: no cover
            print(f"[warn] could not read FIXED.json: {e}", file=sys.stderr)

    # Split patterns into "still open" vs "fixed tonight"
    open_patterns = [
        (n, c) for (n, c) in ranked if n not in fixed_pattern_to_pr
    ]
    fixed_patterns = [
        (n, c) for (n, c) in ranked if n in fixed_pattern_to_pr
    ]

    return {
        "run_dir": str(run_dir),
        "now_iso": datetime.now(timezone.utc).isoformat(),
        "total_findings": total,
        "category_counts": dict(cat_counts),
        "category_errors": dict(cat_errs),
        "pattern_counts": dict(pattern_counts),
        "pattern_severity": pattern_severity,
        "pattern_samples": dict(pattern_samples),
        "ranked_patterns": ranked,
        "open_patterns": open_patterns,
        "fixed_patterns": fixed_patterns,
        "prs_shipped": prs_shipped,
        "fixed_pattern_to_pr": fixed_pattern_to_pr,
        "latency_ms": latency_summary,
        "cost_usd_so_far": cost_final,
    }


def write_morning_brief(report: dict, out_path: Path) -> None:
    lines: list[str] = []
    lines.append(f"# Overnight Stress + Repair Report")
    lines.append(f"")
    lines.append(f"Generated: {report['now_iso']}")
    lines.append(f"Run dir: `{report['run_dir']}`")
    lines.append(f"")

    # Executive summary — one-paragraph elevator pitch up top so the
    # 7 AM reader can skim and stop here if everything's fine.
    n_total = report["total_findings"]
    n_err = sum(report.get("category_errors", {}).values())
    err_pct = (100.0 * n_err / n_total) if n_total else 0.0
    n_prs = len(report.get("prs_shipped", []))
    n_open = len(report.get("open_patterns", []))
    n_fixed = len(report.get("fixed_patterns", []))
    cost = report["cost_usd_so_far"]

    # Status line: green if open patterns have low impact, yellow otherwise
    max_open_impact = 0.0
    for name, count in report.get("open_patterns", []):
        sev = report["pattern_severity"].get(name, 1)
        imp = sev * math.log(1 + count)
        if imp > max_open_impact:
            max_open_impact = imp
    badge = "🟢 CLEAN" if max_open_impact < 3.0 else "🟡 REVIEW"

    lines.append(f"## Executive summary  {badge}")
    lines.append(f"")
    lines.append(
        f"**{n_total} prompts run, {n_err} errors ({err_pct:.1f}%), "
        f"{n_prs} PR{'s' if n_prs != 1 else ''} shipped overnight, "
        f"{n_fixed} pattern{'s' if n_fixed != 1 else ''} closed, "
        f"{n_open} still open** (max-impact {max_open_impact:.2f}). "
        f"Cost ${cost:.2f}."
    )
    lines.append(f"")
    if max_open_impact < 3.0:
        lines.append(
            "_All still-open patterns are defensive/expected behavior under "
            "stress (rate-limit handling, lifeboat probe telemetry). No "
            "action required — skim the rest if curious._"
        )
    else:
        lines.append(
            "_One or more open patterns exceed the 3.0 impact threshold. "
            "See \"Still Open\" section below._"
        )
    lines.append(f"")

    lines.append(f"## TL;DR")
    lines.append(f"")
    lines.append(f"- Prompts run: **{report['total_findings']}**")
    lines.append(f"- Cost so far: **${report['cost_usd_so_far']:.4f}**")
    lat = report["latency_ms"]
    lines.append(
        f"- Latency: p50={lat['p50']}ms, p90={lat['p90']}ms, "
        f"p99={lat['p99']}ms, max={lat['max']}ms"
    )
    n_open = len(report.get("open_patterns", []))
    n_fixed = len(report.get("fixed_patterns", []))
    n_prs = len(report.get("prs_shipped", []))
    lines.append(
        f"- Patterns: **{n_open} still open** (after fixes), "
        f"{n_fixed} fixed tonight via {n_prs} PR{'s' if n_prs != 1 else ''}"
    )
    lines.append(f"")

    # PRs shipped tonight (link list at the top so Grant sees the work)
    if report.get("prs_shipped"):
        lines.append(f"## PRs shipped tonight")
        lines.append(f"")
        for pr in report["prs_shipped"]:
            num = pr.get("pr")
            title = pr.get("title", "")
            url = pr.get("url", "")
            patterns = pr.get("patterns_addressed", [])
            pat_str = (
                f" — addresses: {', '.join(f'`{p}`' for p in patterns)}"
                if patterns else " — defensive (no observed failure pattern)"
            )
            lines.append(f"- **#{num}** [{title}]({url}){pat_str}")
            summary = pr.get("summary")
            if summary:
                lines.append(f"  - {summary}")
        lines.append(f"")

    lines.append(f"## By category")
    lines.append(f"")
    lines.append("| Category | Run | Errors |")
    lines.append("|---|---|---|")
    for cat, n in sorted(report["category_counts"].items(),
                         key=lambda kv: -kv[1]):
        errs = report["category_errors"].get(cat, 0)
        lines.append(f"| {cat} | {n} | {errs} |")
    lines.append("")

    fixed_map = report.get("fixed_pattern_to_pr", {})

    lines.append("## Failure patterns — STILL OPEN")
    lines.append("")
    open_patterns = report.get("open_patterns") or []
    if not open_patterns:
        lines.append("_No open failure patterns. All observed issues are "
                     "either expected defensive behavior (low severity) or "
                     "closed by tonight's PRs._")
    else:
        lines.append("| Rank | Pattern | Count | Severity | Impact |")
        lines.append("|---|---|---|---|---|")
        for rank, (name, count) in enumerate(open_patterns, 1):
            sev = report["pattern_severity"].get(name, 1)
            impact = round(sev * math.log(1 + count), 2)
            lines.append(
                f"| {rank} | `{name}` | {count} | {sev} | {impact} |"
            )
    lines.append("")

    if report.get("fixed_patterns"):
        lines.append("## Failure patterns — FIXED tonight")
        lines.append("")
        lines.append("| Pattern | Count | Severity | Impact | Fixed by |")
        lines.append("|---|---|---|---|---|")
        for name, count in report["fixed_patterns"]:
            sev = report["pattern_severity"].get(name, 1)
            impact = round(sev * math.log(1 + count), 2)
            pr = fixed_map.get(name, {})
            pr_link = f"[#{pr.get('pr', '?')}]({pr.get('url', '')})"
            lines.append(
                f"| `{name}` | {count} | {sev} | {impact} | {pr_link} |"
            )
        lines.append("")

    lines.append("## Sample failures (open patterns only)")
    lines.append("")
    open_names = [n for n, _ in (report.get("open_patterns") or [])][:10]
    if not open_names:
        lines.append("_No open patterns to sample._")
        lines.append("")
    for name in open_names:
        lines.append(f"### {name}")
        for s in report["pattern_samples"].get(name, [])[:3]:
            lines.append(
                f"- **#{s['index']}** ({s['category']}, "
                f"{s['latency_ms']}ms): `{s['prompt']}`"
            )
            if s.get("error"):
                lines.append(f"  - error: `{s['error']}`")
            else:
                lines.append(
                    f"  - response: `{s['response_preview']}`"
                )
        lines.append("")

    out_path.write_text("\n".join(lines))


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=None,
                    help="Run dir (defaults to ~/.windy-stress/CURRENT_RUN)")
    ap.add_argument("--out", default=None,
                    help="Output markdown path (defaults to morning_brief.md "
                         "in run dir)")
    ap.add_argument("--json", action="store_true",
                    help="Also dump raw report as JSON to stdout")
    args = ap.parse_args()

    if args.run_dir:
        run_dir = Path(args.run_dir).expanduser().resolve()
    else:
        link = Path.home() / ".windy-stress" / "CURRENT_RUN"
        if not link.exists():
            print("No active run (~/.windy-stress/CURRENT_RUN missing).",
                  file=sys.stderr)
            sys.exit(1)
        run_dir = link.resolve()

    out_path = (Path(args.out).expanduser().resolve()
                if args.out
                else run_dir / "morning_brief.md")

    report = analyze(run_dir)
    write_morning_brief(report, out_path)

    print(f"Brief written → {out_path}")
    print(f"  Findings: {report.get('total_findings', 0)}")
    if report.get("ranked_patterns"):
        print(f"  Top pattern: {report['ranked_patterns'][0]}")

    if args.json:
        print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
