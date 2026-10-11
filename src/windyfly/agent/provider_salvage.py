"""Move provider keys out of the retired provider files, once, at brain start (Wave 3, AG/Hub 2026-10-11).

Until Wave 3 a provider key could sit in four plain files besides the environment:

- the old dashboard's ``data/providers.json`` (its active key was copied in as ``api_key``),
  ``data/provider_keys.json`` (its multi-key store) and ``data/provider_notes.json`` (free text,
  where an owner may have pasted a key);
- the brain's own ``~/.windy/providers.json`` (an ``api_key`` copied there by the old cwd
  migration, or typed by hand).

Now keys live only in the environment: the private env file the runtime loads (``WINDY_ENV_FILE``,
else the project ``.env``). Salvage, then scrub:

1. For each provider with a key in one of those files and NO key in the environment or the env
   file, write it into the env file (atomic, 0600) and hot-load it for this run. A provider that
   already has an env key keeps it: the file copy is just dropped (env wins).
2. Only if every write worked: rewrite ``~/.windy/providers.json`` without keys (0600), keep the
   owner's notes in ``~/.windy/retired-provider-notes.json`` (0600, key-shaped text replaced),
   then overwrite the old dashboard files with zeros and delete them. If any write failed, NOTHING
   is rewritten or deleted: the keys are hot-loaded for this run and the salvage runs again at
   the next start.

Logs name providers and counts only, never a key. Symlinks are never followed: a symlinked file
is left alone (and so is everything else, because its key could not be checked). A file that
does not parse is left alone too.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
from collections.abc import MutableMapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

GATEWAY_FILES = ("providers.json", "provider_keys.json", "provider_notes.json")
NOTES_KEPT = "retired-provider-notes.json"
MOVED = "[moved to the env file]"
REMOVED = "[key-shaped text removed]"
# Non-secret fields of a custom provider defined only in the old dashboard file.
_DEFINITION_FIELDS = ("name", "type", "base_url", "models", "api_key_env")
# A value safe to write as a bare KEY=VALUE line for both python-dotenv and systemd.
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9._~+/=:@-]+$")
# Key-shaped tokens in a free-text note: known provider prefixes, or one long unbroken token.
_NOTE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-])("
    r"sk-[A-Za-z0-9_-]{20,}|gsk_[A-Za-z0-9]{20,}|xai-[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{30,}"
    r"|pplx-[A-Za-z0-9]{20,}|fw_[A-Za-z0-9]{20,}|[A-Za-z0-9_-]{40,}"
    r")(?![A-Za-z0-9_-])"
)


def default_env_var(provider: str) -> str:
    """WINDY_PROVIDER_<NAME>_API_KEY with the name reduced to [A-Z0-9_]."""
    name = re.sub(r"[^A-Z0-9]+", "_", provider.upper()).strip("_") or "CUSTOM"
    return f"WINDY_PROVIDER_{name}_API_KEY"


def _read_json(path: Path) -> tuple[Any, str | None]:
    """(data, problem). Never follows a symlink."""
    try:
        st = path.lstat()
    except FileNotFoundError:
        return None, None
    if stat.S_ISLNK(st.st_mode):
        return None, "is a symlink (not followed)"
    if not stat.S_ISREG(st.st_mode):
        return None, "is not a regular file"
    try:
        return json.loads(path.read_text(encoding="utf-8") or "{}"), None
    except (OSError, ValueError) as exc:
        return None, f"could not be read ({type(exc).__name__})"


def _env_file_vars(env_file: Path) -> set[str]:
    """Names with a non-empty value in the env file."""
    try:
        text = env_file.read_text(encoding="utf-8")
    except OSError:
        return set()
    names = set()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, sep, value = line.partition("=")
        if sep and value.strip().strip('"').strip("'"):
            names.add(name.strip())
    return names


def _zero_and_delete(path: Path) -> None:
    """Overwrite with zeros, then delete. Cleanup, not a secure erase (SSD / copy-on-write)."""
    with open(path, "r+b") as fh:
        size = os.fstat(fh.fileno()).st_size
        if size:
            fh.write(b"\0" * size)
            fh.flush()
            os.fsync(fh.fileno())
    path.unlink()


def _write_private_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.salvage-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2))
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _keep_notes(path: Path, notes: dict[str, Any], used: dict[str, str], *, moved: set[str]) -> bool:
    """Keep the owner's own words (Hub 10-11): every note, key-shaped text replaced. True if safe
    to delete the original (nothing to keep, or written). Never overwrites a note kept earlier."""
    kept = {}
    for provider, note in notes.items():
        if not isinstance(note, str) or not note.strip():
            continue

        def mask(m: re.Match[str], provider: str = provider) -> str:
            moved_here = used.get(provider) == m.group(1) and provider in moved
            return MOVED if moved_here else REMOVED

        kept[provider] = _NOTE_TOKEN.sub(mask, note)
    if not kept:
        return True
    try:
        existing, problem = _read_json(path)
        if problem:
            raise OSError(problem)
        merged = dict(existing) if isinstance(existing, dict) else {}
        for provider, note in kept.items():
            merged.setdefault(provider, note)
        _write_private_json(path, merged)
    except OSError as exc:
        logger.warning("provider salvage: could not keep the provider notes in %s (%s); leaving "
                       "data/provider_notes.json in place", path, type(exc).__name__)
        return False
    logger.info("provider salvage: kept %d provider note(s) in %s (key-shaped text replaced)",
                len(kept), path)
    return True


@contextmanager
def _lock(state_dir: Path) -> Iterator[None]:
    """One salvage at a time: two channel runtimes (matrix + telegram) start together."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows: one runtime per install in practice
        yield
        return
    state_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(state_dir / ".provider-salvage.lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def salvage_provider_keys(
    *,
    state_file: Path | None = None,
    gateway_dirs: list[Path] | None = None,
    env_file: Path | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run the salvage. Returns names and counts only (no key values)."""
    from windyfly.agent.providers import BUILTIN_PROVIDERS, _overrides_path

    if state_file is None:
        state_file = _overrides_path()[0]
    if gateway_dirs is None:
        from windyfly.platform import get_project_root

        gateway_dirs = [get_project_root() / "data", Path.cwd() / "data"]
    if env_file is None:
        from windyfly.bring_home import env_file_path

        env_file = env_file_path()
    env = os.environ if environ is None else environ

    with _lock(state_file.parent):
        return _salvage(state_file, gateway_dirs, env_file, env, BUILTIN_PROVIDERS)


def _salvage(
    state_file: Path,
    gateway_dirs: list[Path],
    env_file: Path,
    env: MutableMapping[str, str],
    builtins: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    report: dict[str, Any] = {"moved": [], "dropped": [], "failed": [], "removed": [], "left": []}

    # Distinct, existing old-dashboard dirs (project data/ and the cwd's data/ can be the same).
    seen: set[Path] = set()
    dirs = []
    for d in gateway_dirs:
        try:
            real = d.resolve()
        except OSError:
            continue
        if real not in seen:
            seen.add(real)
            dirs.append(d)

    problems: list[str] = []
    state, problem = _read_json(state_file)
    if problem:
        problems.append(f"{state_file.name} {problem}")
    state = state if isinstance(state, dict) else {}
    gw: dict[str, dict[str, Any]] = {name: {} for name in GATEWAY_FILES}
    gw_paths: list[Path] = []
    for d in dirs:
        for name in GATEWAY_FILES:
            path = d / name
            data, problem = _read_json(path)
            if problem:
                problems.append(f"data/{name} {problem}")
                continue
            if data is None:
                continue
            gw_paths.append(path)
            if isinstance(data, dict):
                for k, v in data.items():
                    gw[name].setdefault(k, v)

    # Candidate key per provider, by precedence: the brain's own file (what ran until now),
    # the old dashboard's active key, its providers.json copy, a key-shaped token in a note.
    candidates: dict[str, str] = {}
    note_ambiguous: list[str] = []

    def offer(provider: str, value: Any) -> None:
        if isinstance(value, str) and value.strip() and provider not in candidates:
            candidates[provider] = value.strip()

    for provider, entry in state.items():
        if isinstance(entry, dict):
            offer(provider, entry.get("api_key"))
    for provider, keys in gw["provider_keys.json"].items():
        if isinstance(keys, list):
            dicts = [k for k in keys if isinstance(k, dict)]
            active = [k for k in dicts if k.get("active")] or dicts
            if active:
                offer(provider, active[0].get("key_full"))
    for provider, entry in gw["providers.json"].items():
        if isinstance(entry, dict) and provider != "_settings":
            offer(provider, entry.get("api_key"))
    note_used: dict[str, str] = {}
    for provider, note in gw["provider_notes.json"].items():
        if isinstance(note, str) and provider not in candidates:
            tokens = _NOTE_TOKEN.findall(note)
            if tokens:
                offer(provider, tokens[0])
                note_used[provider] = tokens[0]
                if len(tokens) > 1:
                    note_ambiguous.append(provider)

    # Custom providers the old dashboard defined: keep their non-secret definition.
    definitions: dict[str, dict[str, Any]] = {}
    for provider, entry in gw["providers.json"].items():
        if provider == "_settings" or not isinstance(entry, dict):
            continue
        if provider in builtins or provider in state:
            continue
        definitions[provider] = {f: entry[f] for f in _DEFINITION_FIELDS if f in entry}
        definitions[provider].setdefault("type", "openai")
    if isinstance(gw["providers.json"].get("_settings"), dict):
        logger.info("provider salvage: the old dashboard's active-model setting is not kept "
                    "(the model comes from windyfly.toml or Windy Mind)")

    file_vars = _env_file_vars(env_file)
    env_names: dict[str, str] = {}
    for provider, value in candidates.items():
        defined = state[provider] if isinstance(state.get(provider), dict) else {}
        var = (defined.get("api_key_env") or (builtins.get(provider) or {}).get("api_key_env")
               or (definitions.get(provider) or {}).get("api_key_env") or default_env_var(provider))
        env_names[provider] = var
        if env.get(var, "").strip() or var in file_vars:
            report["dropped"].append(provider)
            continue
        if not _SAFE_VALUE.match(value):
            report["failed"].append(provider)
            env[var] = value  # keep it working this run
            continue
        try:
            from windyfly.agent.capabilities.setup import _atomic_upsert_env_var

            _atomic_upsert_env_var(env_file, var, value)
        except OSError:
            report["failed"].append(provider)
            env[var] = value
            continue
        env[var] = value
        report["moved"].append(provider)

    if note_ambiguous:
        logger.warning("provider salvage: notes for %s held more than one key-shaped value; "
                       "used the first", ", ".join(sorted(note_ambiguous)))
    if report["moved"]:
        logger.info("provider salvage: moved %d provider key(s) into the private env file %s (%s)",
                    len(report["moved"]), env_file, ", ".join(sorted(report["moved"])))
    if report["dropped"]:
        logger.info("provider salvage: %s already had a key in the environment; the copy in the "
                    "old provider files was dropped", ", ".join(sorted(report["dropped"])))
    if report["failed"] or problems:
        if report["failed"]:
            logger.warning("provider salvage: could not save the key for %s into %s; using it for "
                           "this run only and leaving every provider file as it is (retry at next "
                           "start)", ", ".join(sorted(report["failed"])), env_file)
        for p in problems:
            logger.warning("provider salvage: %s; leaving every provider file as it is", p)
        report["left"] = [p.name for p in gw_paths] + ([state_file.name] if state else [])
        return report

    # Every key is safe in the env: scrub. The brain's own file first, without keys.
    new_state = {}
    for provider, entry in state.items():
        if isinstance(entry, dict):
            entry = {k: v for k, v in entry.items() if k != "api_key"}
            if provider in env_names and provider not in builtins and not entry.get("api_key_env"):
                entry["api_key_env"] = env_names[provider]
        new_state[provider] = entry
    for provider, definition in definitions.items():
        if provider in env_names and not definition.get("api_key_env"):
            definition["api_key_env"] = env_names[provider]
        new_state[provider] = definition
    if new_state != state:
        try:
            _write_private_json(state_file, new_state)
        except OSError as exc:
            logger.warning("provider salvage: could not rewrite %s (%s); leaving the old provider "
                           "files as they are", state_file, type(exc).__name__)
            report["left"] = [p.name for p in gw_paths]
            return report
    notes_ok = _keep_notes(state_file.parent / NOTES_KEPT, gw["provider_notes.json"], note_used,
                           moved=set(report["moved"]))
    for path in gw_paths:
        if path.name == "provider_notes.json" and not notes_ok:
            report["left"].append(path.name)
            continue
        try:
            _zero_and_delete(path)
            report["removed"].append(path.name)
        except OSError as exc:
            logger.warning("provider salvage: could not remove retired data/%s (%s)",
                           path.name, type(exc).__name__)
            report["left"].append(path.name)
    if report["removed"]:
        logger.info("provider salvage: removed the old dashboard's %s",
                    ", ".join(f"data/{n}" for n in report["removed"]))
    return report
