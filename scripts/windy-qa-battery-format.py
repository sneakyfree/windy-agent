"""Read the v13 / v14 latest summary JSONs and format a one-message
Telegram brief for the bot's owner.

Called by windy-qa-battery.sh after the harnesses run. Outputs to
stdout; the shell script ships it via the Telegram sendMessage API.

Tone: same friendly-grandma format as windy-weekly-brief — lead
with the headline (all-green / regression / error), then per-
category breakdown, then any specific failure detail. Markdown
escaped for Telegram (no underscores in bare names, no asterisks
inside code spans).

Cost: ~free (just file reads).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LOG_DIR = Path("/home/grantwhitmer/.windy-stress/logs")


def _latest(prefix: str) -> dict | None:
    files = sorted(LOG_DIR.glob(f"{prefix}_*.summary.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text())
    except Exception:
        return None


def _category_glyph(s: dict) -> str:
    if s["fail"] == 0:
        return "✅"
    if s["pass"] > s["fail"]:
        return "⚠️"
    return "❌"


def _format_battery_block(label: str, data: dict | None, exit_code: int) -> list[str]:
    lines = [f"*{label}*"]
    if data is None:
        lines.append("  (no summary file found — run may have errored before writing)")
        if exit_code != 0:
            lines.append(f"  exit code: {exit_code}")
        return lines

    total = data.get("total", 0)
    passed = data.get("passed", 0)
    pct = 100.0 * passed / total if total else 0.0
    glyph = "🟢" if pct == 100 else ("🟡" if pct >= 80 else "🔴")
    lines.append(f"  {glyph} {passed}/{total}  ({pct:.1f}%)")
    lines.append(f"  model: `{data.get('model', '?')}`")

    by_cat = data.get("by_category", {})
    if by_cat:
        lines.append("  per-category:")
        for cat in sorted(by_cat.keys()):
            s = by_cat[cat]
            tot = s["pass"] + s["fail"]
            lines.append(f"    {_category_glyph(s)} {cat}: {s['pass']}/{tot}")

    fails = [r for r in data.get("results", []) if not r.get("passed")]
    if fails:
        lines.append("  failures:")
        for f in fails[:5]:
            cat = f.get("category", "?")
            prompt = (f.get("prompt") or "")[:60]
            reasons = ", ".join(f.get("failures", [])[:2])
            lines.append(f"    • [{cat}] {prompt}…")
            if reasons:
                lines.append(f"      ↳ {reasons[:120]}")
        if len(fails) > 5:
            lines.append(f"    (… and {len(fails) - 5} more)")

    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v13-exit", type=int, default=0)
    ap.add_argument("--v14-exit", type=int, default=0)
    ap.add_argument("--skip-v14", default="0")
    args = ap.parse_args()

    skip_v14 = args.skip_v14 != "0"

    v13 = _latest("v13_qa_battery")
    v14 = _latest("v14_extended") if not skip_v14 else None

    # Headline: rank by worst run.
    runs_data = [v13]
    if not skip_v14:
        runs_data.append(v14)

    pcts = []
    for d in runs_data:
        if d and d.get("total"):
            pcts.append(100.0 * d.get("passed", 0) / d["total"])

    if not pcts:
        headline = "🚨 *Windy QA battery — both runs missing summaries*"
    else:
        worst = min(pcts)
        if worst == 100:
            headline = "🟢 *Windy QA battery — all green*"
        elif worst >= 95:
            headline = "🟡 *Windy QA battery — minor regression*"
        elif worst >= 80:
            headline = "🟠 *Windy QA battery — multiple regressions*"
        else:
            headline = "🔴 *Windy QA battery — material regression*"

    out = [headline, ""]
    out += _format_battery_block("v13 (sanity / breadth / hostile)", v13, args.v13_exit)
    out.append("")
    if skip_v14:
        out.append("_v14 skipped this run (SKIP_V14=1)_")
    else:
        out += _format_battery_block(
            "v14 (multi-lang / injection / crisis / privacy / tools)",
            v14, args.v14_exit,
        )

    # Footer
    out.append("")
    out.append("_Re-run by hand: bash ~/.local/bin/windy-qa-battery.sh_")

    msg = "\n".join(out)
    # Telegram caps message at 4096 chars; truncate gracefully.
    if len(msg) > 4000:
        msg = msg[:3950] + "\n\n_(brief truncated — see harness logs in ~/.windy-stress/logs/)_"
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
