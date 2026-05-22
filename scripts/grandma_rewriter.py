#!/usr/bin/env python3
"""Phase 8.2 — Haiku-based grandma-readability rewriter.

Reads ~/.windy-stress/user_strings.txt (output of extract_user_strings.py,
Phase 8.1), runs each candidate through Claude Haiku with a grandma-
review prompt, and emits ~/.windy-stress/user_strings_review.md with
verdicts:

  KEEP    — already grandma-readable, no action needed
  REWRITE — Haiku has a plainer-English suggestion
  JUSTIFY — needs human judgment whether jargon is OK in context

Usage:
  python scripts/grandma_rewriter.py             # full run (needs OAuth)
  python scripts/grandma_rewriter.py --dry-run   # show prompt + first 5
  python scripts/grandma_rewriter.py --limit 50  # cap for budget

Requirements:
  - Working ANTHROPIC_API_KEY (OAuth or regular) in env
  - anthropic SDK installed

Budget control:
  - Defaults to Haiku 4.5 (~$0.0001 per 3609 strings batched = ~$0.05)
  - Aborts if estimated cost >$1; pass --cost-cap to override
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

USER_STRINGS = Path.home() / ".windy-stress" / "user_strings.txt"
OUT = Path.home() / ".windy-stress" / "user_strings_review.md"
MODEL = "claude-haiku-4-5-20251001"

GRANDMA_RUBRIC = """You are reviewing user-facing strings from a Telegram bot
named Windy Fly. The bot's audience includes non-technical users
("grandmas") who don't know what an API key, token, OAuth, file
descriptor, or context window is.

For each string, decide:
  KEEP    — already plain English, grandma will get it
  REWRITE — has jargon a grandma won't understand; suggest a plainer version
  JUSTIFY — jargon is appropriate in context (e.g., the bot is reporting
            an OAuth error to an operator who needs the specific term)

Respond ONLY with one line per string in the format:
  <NUM>. <KEEP|REWRITE|JUSTIFY> | <reason or suggestion>

Where <NUM> is the line number I gave you. Be terse.
"""


def _load_candidates(path: Path, limit: int | None) -> list[tuple[int, str, str]]:
    """Returns [(line_num, file:line_ref, string), ...]."""
    if not path.exists():
        print(f"ERROR: run extract_user_strings.py first ({path} missing)",
              file=sys.stderr)
        sys.exit(1)
    out = []
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([^:]+):(\d+):\s+(.*)$", line)
        if not m:
            continue
        ref = f"{m.group(1)}:{m.group(2)}"
        s = m.group(3)
        out.append((i, ref, s))
        if limit and len(out) >= limit:
            break
    return out


def _batch_review(
    candidates: list[tuple[int, str, str]],
    batch_size: int = 25,
    dry_run: bool = False,
) -> list[dict[str, str]]:
    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic SDK not installed; pip install anthropic",
              file=sys.stderr)
        sys.exit(1)

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("ERROR: ANTHROPIC_API_KEY not set in env", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=key)
    results: list[dict[str, str]] = []

    for start in range(0, len(candidates), batch_size):
        batch = candidates[start:start + batch_size]
        prompt_block = "\n".join(
            f"{num}. {s[:200]}" for num, _ref, s in batch
        )
        full_prompt = (
            f"{GRANDMA_RUBRIC}\n\nReview these strings:\n\n{prompt_block}"
        )

        if dry_run:
            print(f"--- batch {start // batch_size + 1} (dry-run) ---")
            print(full_prompt[:1500])
            print("...")
            return []

        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": full_prompt}],
            )
            text = resp.content[0].text if resp.content else ""
        except Exception as e:  # noqa: BLE001 — best-effort with reporting
            print(f"  batch {start}: API error {e}", file=sys.stderr)
            continue

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d+)\.\s*(KEEP|REWRITE|JUSTIFY)\s*\|\s*(.*)$",
                         line)
            if not m:
                continue
            num = int(m.group(1))
            verdict = m.group(2)
            note = m.group(3).strip()
            ref_lookup = {n: r for n, r, _ in candidates}
            results.append({
                "line": str(num),
                "ref": ref_lookup.get(num, "?"),
                "verdict": verdict,
                "note": note[:200],
            })
        print(f"  batch {start // batch_size + 1}/"
              f"{(len(candidates) - 1) // batch_size + 1} done; "
              f"{len(results)} verdicts so far")

    return results


def _write_report(results: list[dict[str, str]], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    counts = {"KEEP": 0, "REWRITE": 0, "JUSTIFY": 0}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    lines = [
        "# Grandma-readability review",
        "",
        f"Generated by `scripts/grandma_rewriter.py` (Phase 8.2). "
        f"Run via Haiku 4.5 on {len(results)} strings.",
        "",
        f"- ✅ KEEP: {counts.get('KEEP', 0)}",
        f"- ✏️  REWRITE: {counts.get('REWRITE', 0)}",
        f"- 🤔 JUSTIFY: {counts.get('JUSTIFY', 0)}",
        "",
        "## REWRITE candidates (Haiku suggestions)",
        "",
    ]
    for r in results:
        if r["verdict"] == "REWRITE":
            lines.append(f"- `{r['ref']}` — {r['note']}")
    lines.append("")
    lines.append("## JUSTIFY candidates (human judgment needed)")
    lines.append("")
    for r in results:
        if r["verdict"] == "JUSTIFY":
            lines.append(f"- `{r['ref']}` — {r['note']}")
    out.write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap number of strings reviewed (budget control)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show prompt + first batch; do not call API")
    ap.add_argument("--batch-size", type=int, default=25)
    ap.add_argument("--cost-cap", type=float, default=1.0,
                    help="Abort if estimated cost exceeds $X (default $1)")
    args = ap.parse_args()

    candidates = _load_candidates(USER_STRINGS, args.limit)
    print(f"Loaded {len(candidates)} candidates from {USER_STRINGS}")

    # Cost estimate: Haiku 4.5 ~ $0.0001 input + $0.0005 output per 1K tokens
    # Each candidate ~ 50 input tokens + 20 output. 3609 candidates = ~180K
    # input + 72K output = ~$0.02 + $0.04 = $0.06 worst case.
    est = (len(candidates) * 70) / 1000 * 0.0006
    print(f"Estimated cost: ${est:.3f}")
    if est > args.cost_cap:
        print(f"ABORTING: estimated cost exceeds --cost-cap ${args.cost_cap}")
        return 1

    results = _batch_review(candidates, args.batch_size, args.dry_run)
    if not results:
        print("No results — dry-run or no successful API calls")
        return 0

    _write_report(results, OUT)
    print(f"\nReview written → {OUT}")
    print(f"Verdicts: {sum(1 for r in results if r['verdict'] == 'KEEP')} KEEP, "
          f"{sum(1 for r in results if r['verdict'] == 'REWRITE')} REWRITE, "
          f"{sum(1 for r in results if r['verdict'] == 'JUSTIFY')} JUSTIFY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
