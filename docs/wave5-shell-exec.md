# Wave 5 — Shell Exec Design

**Status:** Design draft. No code yet. Awaiting Grant's decisions on the
open questions below before implementation.

**Scope:** First arbitrary-shell-command capability for the agent —
`shell.exec` (and the supporting Docker/sandbox dispatch infrastructure).
Wave 5 is what closes the agency gap with OpenClaw and Hermes; both ship
shell-by-default-on-host. We ship shell-by-default-in-Docker, with host
exec available to OWNER band only after explicit pairing.

**Why this is the most security-critical PR in the wave plan:** every
prior capability has a narrow blast radius (filesystem allowlist + always-
deny). Shell breaks out of that. A sloppy default here is the catastrophic
incident every agent framework eventually has — OpenClaw's `auth-profiles.
json` plaintext-secrets episode, Hermes' SECURITY.md *telling operators
not to use the default mode*. Don't repeat their mistake.

**Architectural anchors:**

- **`SandboxTier` from `descriptor.py`** — `none` / `host_readonly` /
  `host_rw` / `docker` / `remote` already exist as string constants. Wave
  5 implements the `docker` tier for real (Wave 4 implements `host_rw`).
- **Wave 4's undo log** — shell can't be undone in general (`rm -rf`
  doesn't have an inverse), so shell's audit row records *what was run*
  but doesn't claim reversibility.
- **Wave 4's runtime tier escalation hook** — shell uses it heavily: a
  command containing `rm` or `sudo` or `> /dev/sda` should bump the tier
  even at OWNER band.

---

## Decision 1: Sandbox tier defaults per band

What's the default execution environment for `shell.exec` per band?

| Band | Recommended default | Override available? |
|---|---|---|
| SANDBOX | denied (tier gate) | no |
| USER | denied (Tier 5 requires TRUSTED+) | no |
| TRUSTED | **Docker** (read-only mount of allowed_roots) | yes, via `sandbox=host_rw` arg + paired-device confirmation |
| OWNER | **Docker** by default, host on explicit `sandbox=host_rw` arg | yes |

**Recommendation:** Even OWNER bands get Docker by default. The friction
of typing `{"sandbox": "host_rw"}` to escape the sandbox is the right
amount of friction — it forces the LLM (or the user) to consciously opt
into the larger blast radius. Hermes ships `local` as default and
*tells operators in SECURITY.md to switch it off* — that's an admission
that the default is wrong. We can do better by defaulting to safe.

**Implementation note:** the Docker container is built once on first call
from a minimal base image (`alpine:3.19` plus `bash`, `coreutils`, `git`,
`grep`, `findutils`, `curl`) and cached as `windyfly-shell-sandbox:latest`.
Subsequent calls reuse the image; container-per-call (`docker run --rm`)
keeps state contained.

**Open question for Grant:** Should we instead use a single long-lived
container that the agent reuses across calls (faster startup, accumulates
state) rather than container-per-call? Faster but state leaks between
turns. Container-per-call is the safer default.

---

## Decision 2: Bind-mount scope when in Docker

What gets mounted into the sandbox?

**Option A — Just `allowed_roots` from `filesystem.py`.** Mount each
allowed root read-write (or read-only — see Decision 5). The shell can
only act on files the read-side capabilities could already see.

**Option B — A scratch dir + project subset.** Mount a clean
`~/.windy/sandbox/scratch/` read-write plus the user-specified project dir
read-only. Anything the agent wants to *write* has to land in scratch
first; explicit move-to-project step required.

**Option C — Nothing by default; LLM passes mount specs per call.** Most
restrictive; LLM declares `{"mounts": ["/Users/grant/projects/foo"]}` per
invocation.

**Recommendation: Option A — mount `allowed_roots` from the existing
filesystem allowlist.** Symmetry with Wave 3's read access — the agent
sees the same world for read and shell, just gated by tier. Makes the
mental model crisp: "if `fs.read_file` could read it, `shell.exec` can
operate on it."

Same `_resolve_and_check` from `filesystem.py` validates each command's
referenced paths *if* the command can be parsed (heuristic — for most
common commands like `ls`, `cat`, `grep`, `git`, `find` we can extract
path args; for compound commands we mount and rely on the sandbox's own
filesystem isolation).

The always-deny list (`.ssh`, `.aws`, `.env`, etc.) is enforced by *not
mounting* those paths into the container. Inside the sandbox, those paths
literally don't exist — even `ls ~/.ssh` returns "No such file or
directory" rather than secrets.

**Open question for Grant:** Mount allowed_roots read-only or read-write
by default? Read-only forces the agent to use `fs.write_file` for any
modification (which has its own audit + undo). Read-write lets shell
modify directly (faster, but bypasses the undo log). Recommend read-only
for the first cut; flip to read-write only if it proves too restrictive.

---

## Decision 3: Command allowlist vs. blocklist

Are there per-band restrictions on *which commands* run inside the
sandbox?

**Option A — No allowlist; rely entirely on sandbox isolation.** Inside
Docker the shell can do anything; outside the container nothing leaks.
Fastest, most flexible.

**Option B — Per-band allowlist.** USER (when allowed) gets `ls cat grep
find git diff stat wc head tail` only. TRUSTED gets that plus `mv cp mkdir
touch sed awk`. OWNER gets full pass-through.

**Option C — Blocklist of dangerous patterns.** Reject commands matching
`rm -rf`, `sudo`, `chmod 777`, `dd if=`, `:(){:|:&};:` (fork bomb),
`curl | sh`, `wget | bash`. Defense in depth.

**Recommendation: Option A as the primary, Option C as a small belt-and-
suspenders.** The Docker isolation is the real defense. Within the
container, what the agent can do to itself doesn't matter — it's a
disposable container with read-only mounts. The blocklist patterns
(Option C) catch a few things that *are* still bad inside the container
(fork bomb starves the host CPU briefly; `curl | sh` running random
malware that exfiltrates allowed_roots even via read-only is
theoretically possible).

So: no command allowlist. Small blocklist of the four highest-leverage
patterns. Rely on sandbox isolation for everything else.

**Open question for Grant:** Comfortable with no command allowlist, or
do you want the per-band restriction? The allowlist is more conservative
but adds maintenance burden (every new safe command someone wants needs
PR review).

---

## Decision 4: Output capture

How big can the captured output be? What about long-running commands?

**Recommendation:**

- **Cap stdout + stderr at 64KB combined** (matches `fs.read_file` max).
- **Truncated output gets a hint:** `{"stdout": "...", "stdout_truncated":
  true, "stdout_total_bytes": 12_500_000}`. The LLM can then chunked-read
  via `fs.read_file` if the output was actually written somewhere.
- **Wall-clock cap of 30 seconds by default.** If the command exceeds it,
  send SIGTERM, give 5s grace, then SIGKILL. Return `{"timed_out": true,
  "stdout": "...", "stderr": "..."}` with whatever was captured.
- **Memory cap of 512MB per container** (`docker run --memory=512m`).
- **No streaming for v1.** Buffered capture only. Streaming complicates the
  capability descriptor (output isn't a single return value) and the audit
  log (when does the row close?). Streaming is a Wave 5 #3 if it proves
  needed.

**Open question for Grant:** OK with 30-second wall-clock cap as default?
Some operations (`git clone`, `npm install`) genuinely need longer.
Per-call override (`{"timeout_s": 120}`) can extend up to a hard ceiling
of 5 minutes — but defaults should be tight.

---

## Decision 5: Failure surfacing

How does a non-zero exit code reach the LLM?

**Recommendation:** capability returns this shape regardless of exit
status:

```json
{
  "command": "git status",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "",
  "stdout_truncated": false,
  "stderr_truncated": false,
  "duration_ms": 142,
  "sandbox_tier": "docker",
  "timed_out": false
}
```

A non-zero exit code is *not* a Python exception — it's data in the
return envelope. The LLM treats it as "the command ran but failed" rather
than "the agent broke." Hermes does this; we should too.

**Real Python exceptions** (the container couldn't start, mount failed,
Docker daemon down) are different — those go through the existing #50
classifier as `TOOL_FAILURE`.

**Open question for Grant:** Should the audit ledger's `success` field
be `1` for any exit code (the *capability* succeeded — it ran the command
and returned the result), or `0` for non-zero exit codes (the *command*
failed)? Recommend the former: capability success ≠ command success;
keep them distinguishable for the optimizer.

---

## Decision 6: The "destructive shell inside Docker" question

`rm -rf /` inside a Docker container with `host_rw` mounts of
`allowed_roots` actually *does* delete those mounted files on the host.
What's the blast radius?

**Mitigations layered:**

1. **Default mount is read-only** (Decision 2 recommendation). `rm -rf`
   inside the container fails with "Read-only file system" for everything
   under `/mnt/allowed_root_*`. Only OWNER + explicit `sandbox=host_rw`
   makes mounts read-write.
2. **Always-deny still applies.** `.ssh`, `.aws`, `.env`, `.windy` are
   never mounted into the container, so they're literally absent.
3. **Blocklist (Decision 3)** rejects `rm -rf` patterns at the dispatcher
   level before the container even starts.
4. **Wave 4's undo log doesn't help here** (no per-file snapshots before
   shell). Shell calls *can't be undone*; that's an explicit property of
   the capability descriptor (`reversibility=Reversibility.WRITE_DESTRUCTIVE`,
   `undo_supported=False`).

So the worst-case blast radius for an OWNER user who explicitly opts into
`sandbox=host_rw` and bypasses the blocklist with creative phrasing:
deletion of files inside `allowed_roots`, minus the always-deny list.
That's the same blast radius as if the user typed `rm -rf` themselves.

**Recommendation:** ship with the layered mitigations above. Accept that
OWNER-band-with-host_rw is a "you asked for it" surface; document
prominently that the agent's blast radius equals the user's own shell
blast radius in that mode.

**Open question for Grant:** Should we add a confirmation prompt (Wave 4
Decision 2 mechanism) for *every* `sandbox=host_rw` shell call regardless
of band, as a hard-coded rule? Belt-and-suspenders that doesn't cost
much.

---

## Scope: what's in Wave 5 #1 vs. deferred

**Wave 5 #1:**

- `shell.exec` capability with the descriptor: `tier=Tier.FULL_MACHINE`,
  default `sandbox_tier="docker"`, `reversibility=WRITE_DESTRUCTIVE`,
  `undo_supported=False`, `dry_run_supported=False` (shell dry-run is
  fundamentally hard — what's the dry-run of `git pull`?).
- Docker dispatcher: builds the image lazily on first call, runs each
  invocation as `docker run --rm --memory=512m --network=none ...` with
  the mounts from Decision 2.
- Network access: `--network=none` by default. Per-call opt-in (`{"network":
  true}`) for OWNER band only — and that's audited as a separate flag in
  `agent_actions`.
- Pre-flight blocklist (Decision 3 small list).
- Tests: blocklist enforcement, mount scope (always-deny inside container),
  exit code passthrough, timeout behavior, output truncation, memory cap.

**Deferred to Wave 5 #2:**

- `shell.exec_streaming` for long-running commands.
- Per-binary command allowlist if Decision 3 changes.
- Persistent container reuse (Decision 1 alternative).
- Browser capability (`browser.navigate`, `browser.click`, etc. via
  Playwright) — same Tier.FULL_MACHINE pattern but a different
  sandbox dispatcher.

**Deferred to Wave 5 #3 or later:**

- Remote sandbox tier (`SandboxTier.REMOTE`) — Modal/Daytona/Singularity
  backends. Useful for true production isolation but huge dependency drag.

---

## Open questions for Grant — decision matrix

| # | Decision | Default if you don't decide |
|---|---|---|
| 1 | Persistent container or container-per-call? | container-per-call |
| 2 | Mounts read-only or read-write by default? | read-only |
| 3 | Command allowlist (per-band) or just blocklist? | just blocklist |
| 4 | 30-second wall-clock default OK? | yes, with 5-min hard ceiling |
| 5 | Audit `success`=1 for non-zero exit, 0 for capability error? | yes |
| 6 | Force confirm on every `sandbox=host_rw` regardless of band? | not built |

If you bless all defaults, Wave 5 #1 implementation is mechanical from
this design. The only piece that needs invention is the Docker dispatcher
glue (~150 lines) — everything else slots into the Capability Plane that
already exists.
