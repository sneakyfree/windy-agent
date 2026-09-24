#!/usr/bin/env python3
"""Fail on credential-shaped strings in tracked files unless they are known FAKES.

Why: this repo is PUBLIC. A real @Windy_0_bot token sat in tests/ as a
"redaction fixture" from 04-21 to 09-24 and the bot was hijacked; a real
(revoked) Anthropic key sat in scripts/fire-drill.sh. Fixtures must be fake.

A match passes only if it contains "FAKE" (preferred: make fakes obvious) or its
sha256 is in .github/lint/secret-shapes-allowlist.json (legacy fixtures, hashes
only so the allowlist never holds a secret). Output never prints a match.
"""
import hashlib, json, pathlib, re, subprocess, sys

PATTERNS = {
    "telegram-bot-token": re.compile(r"(?<![0-9])[0-9]{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"),
    "anthropic-key": re.compile(r"sk-ant-[a-z]+[0-9]{2}-[A-Za-z0-9_-]{20,}"),
}
root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
allow = set(json.loads((root / ".github/lint/secret-shapes-allowlist.json").read_text())["sha256"])
files = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout.split(b"\0")
bad = 0
for f in filter(None, files):
    p = root / f.decode()
    try:
        text = p.read_text(errors="ignore")
    except (IsADirectoryError, FileNotFoundError):
        continue
    for kind, rx in PATTERNS.items():
        for m in rx.finditer(text):
            s = m.group(0)
            h = hashlib.sha256(s.encode()).hexdigest()
            if "FAKE" in s or h in allow:
                continue
            line = text.count("\n", 0, m.start()) + 1
            print(f"::error file={p.relative_to(root)},line={line}::{kind}-shaped string (sha256[:8]={h[:8]}) is not a known fake. "
                  f"Use an obviously fake value containing FAKE, or rotate the credential if it is real.")
            bad += 1
print(f"secret-shapes: {bad} unapproved match(es)")
sys.exit(1 if bad else 0)
