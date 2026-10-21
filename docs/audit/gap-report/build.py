"""Build the gap-analysis data from the strand at four points: the report's baseline, master, Zero, and the
PRs built but not merged. Prints JSON."""
import json
import re
import subprocess
import sys

REPO = sys.argv[1]
DOC = "docs/strand/STRAND_V2.md"


def rows(ref):
    text = subprocess.run(["git", "-C", REPO, "show", f"{ref}:{DOC}"], capture_output=True, text=True, check=True).stdout
    out, section = {}, ""
    for line in text.splitlines():
        if line.startswith("#"):
            section = line.lstrip("#").strip()
            continue
        m = re.match(r"^\| ([A-Z][0-9A-Za-z.]+) \| (.*?) \| (live|retired|planned[^|]*) \| (.*?) \|\s*$", line)
        if m:
            out[m.group(1)] = {"promise": m.group(2), "status": m.group(3).strip(), "test": m.group(4), "section": section}
    return out


base = rows(subprocess.run(["git", "-C", REPO, "rev-parse", "64e3169^"], capture_output=True, text=True).stdout.strip())
master = rows("origin/master")
zero = rows("7311e0e5")
pending = {
    "#592 + follow-up": "fix/explicit-turn-band",
    "safe restore": "fix/safe-restore",
    "cloud storage routes": "feat/cloud-storage-routes",
}
pend_rows = {name: rows(ref) for name, ref in pending.items()}

codes = sorted(set(c for c, r in base.items() if r["status"].startswith("planned")) |
               set(c for c, r in master.items() if r["status"].startswith("planned")) |
               {c for name, pr in pend_rows.items() for c, r in pr.items()
                if master.get(c, {}).get("status") != r["status"] and c in master})

items = []
for c in codes:
    b, m = base.get(c), master.get(c)
    if m is None:
        continue
    pr_live = [name for name, pr in pend_rows.items()
               if c in pr and pr[c]["status"] == "live" and (m["status"] != "live" or pr[c]["promise"] != m["promise"])]
    items.append({
        "code": c, "section": m["section"], "promise": m["promise"],
        "base": b["status"] if b else "(not in strand)", "master": m["status"],
        "zero_live": bool(zero.get(c, {}).get("status") == "live"),
        "pending": pr_live,
        "pending_promise": next((pend_rows[n][c]["promise"] for n in pr_live), None),
    })
print(json.dumps({"items": items,
                  "counts": {"base_rows": len(base), "master_rows": len(master),
                             "base_live": sum(r["status"] == "live" for r in base.values()),
                             "master_live": sum(r["status"] == "live" for r in master.values()),
                             "master_planned": sum(r["status"].startswith("planned") for r in master.values()),
                             "master_retired": sum(r["status"] == "retired" for r in master.values())}},
                 indent=1))
