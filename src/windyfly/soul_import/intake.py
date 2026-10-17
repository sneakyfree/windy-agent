"""Soul-import intake: receive an owner's export file safely (strand P2.21.13; Hub's review A4/A5).

The export (a ChatGPT ``conversations.json`` or a zip of an OpenClaw / Hermes / ChatGPT export) is
untrusted. It is written to a private intake directory on a RAM-backed filesystem when one exists
(``/dev/shm`` or ``$XDG_RUNTIME_DIR``, so the later wipe is real), never in the agent's own folders.
A zip is never extracted as a whole. Only the members a parser reads are copied out, under hard caps:

- **Allowed members:** ``conversations.json``, ``SOUL.md``, ``MEMORY.md``, ``config.yaml``,
  ``config.json``, and ``skills/<name>.md``. Media, databases and everything else are skipped. A
  single common top folder is stripped.
- **Refused zip** (nothing is copied):
  - an absolute path, a ``..``, a backslash or a drive prefix;
  - a NUL in a name;
  - a symlink or any non-regular entry;
  - more than ``MAX_ENTRIES`` entries;
  - a member over ``MAX_MEMBER_BYTES``, a total over ``MAX_TOTAL_BYTES``, or a compression ratio over
    ``MAX_RATIO``.
- Bytes are counted while copying, so a zip that lies about its sizes is stopped too.

``sweep()`` removes intake directories older than ``MAX_AGE_S``; it is called at start so an import cut
short by a restart leaves nothing behind. ``discard()`` overwrites and removes one intake. Nothing here
logs file names or contents, only counts.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import stat
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, BinaryIO

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 100 * 1024 * 1024        # the dashboard cap; chat transports are smaller (20/25 MB)
MAX_ENTRIES = 2000
MAX_MEMBER_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 150 * 1024 * 1024
MAX_RATIO = 100
MAX_AGE_S = 30 * 60
_PREFIX = "windy-soul-intake-"
_ALLOWED_TOP = {"conversations.json", "SOUL.md", "MEMORY.md", "config.yaml", "config.json"}
_CHUNK = 1024 * 1024


class IntakeError(Exception):
    """The upload was refused. The message is safe to show the owner (no file content)."""


@dataclass(frozen=True)
class Intake:
    id: str
    dir: Path          # the folder handed to the parsers (only allowed members inside)


def _base_dir() -> Path:
    for cand in (Path("/dev/shm"), Path(os.environ.get("XDG_RUNTIME_DIR", "") or "/nonexistent")):
        if cand.is_dir() and os.access(cand, os.W_OK):
            return cand
    return Path(tempfile.gettempdir())


def _new_dir() -> tuple[str, Path]:
    intake_id = secrets.token_hex(8)
    d = _base_dir() / f"{_PREFIX}{intake_id}"
    d.mkdir(mode=0o700)
    (d / "export").mkdir(mode=0o700)
    return intake_id, d


def _copy_capped(src: IO[bytes], dst_path: Path, cap: int) -> int:
    total = 0
    with open(dst_path, "wb") as out:
        os.chmod(dst_path, 0o600)
        while True:
            chunk = src.read(_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > cap:
                raise IntakeError(f"the file is larger than {cap // (1024 * 1024)} MB")
            out.write(chunk)
    return total


def _member_target(name: str) -> str | None:
    """The relative path a member is copied to, or None when it is not one we read."""
    p = PurePosixPath(name)
    parts = p.parts
    if len(parts) == 1 and parts[0] in _ALLOWED_TOP:
        return parts[0]
    if len(parts) == 2 and parts[0] == "skills" and parts[1].endswith(".md") and not parts[1].startswith("."):
        return f"skills/{parts[1]}"
    return None


def _check_name(name: str) -> None:
    if "\x00" in name:
        raise IntakeError("the zip has a file name with a NUL character")
    if "\\" in name or name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        raise IntakeError("the zip has a file with an absolute or Windows-style path")
    if ".." in PurePosixPath(name).parts:
        raise IntakeError("the zip has a file that points outside the export")


def _strip_common_root(names: list[str]) -> str:
    """'export/SOUL.md', 'export/skills/a.md' -> 'export/' (one folder that holds everything)."""
    tops = {n.split("/", 1)[0] for n in names if n}
    if len(tops) == 1:
        top = next(iter(tops))
        if all(n.startswith(top + "/") or n == top + "/" for n in names):
            return top + "/"
    return ""


def _from_zip(zip_path: Path, export_dir: Path) -> int:
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise IntakeError("the file is not a valid zip") from e
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise IntakeError(f"the zip has more than {MAX_ENTRIES} files")
        total_declared = 0
        for info in infos:
            # orig_filename is the name as stored: zipfile cuts filename at a NUL, which would hide it.
            _check_name(getattr(info, "orig_filename", info.filename))
            _check_name(info.filename)
            # Unix file type in the high bits: none (plain zip tools) means a regular file; a set type
            # other than a regular file or a folder (a symlink, a device, a pipe) is refused.
            ftype = stat.S_IFMT((info.external_attr >> 16) & 0o177777)
            if ftype and ftype not in (stat.S_IFREG, stat.S_IFDIR):
                raise IntakeError("the zip has a link or a special file")
            if info.file_size > MAX_MEMBER_BYTES:
                raise IntakeError("a file in the zip is too large")
            if info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_RATIO:
                raise IntakeError("a file in the zip is compressed suspiciously well")
            total_declared += info.file_size
        if total_declared > MAX_TOTAL_BYTES:
            raise IntakeError("the zip is too large once unpacked")
        root = _strip_common_root([i.filename for i in infos])
        copied, total = 0, 0
        for info in infos:
            if info.is_dir():
                continue
            rel = info.filename[len(root):] if root and info.filename.startswith(root) else info.filename
            target = _member_target(rel)
            if target is None:
                continue
            dst = export_dir / target
            dst.parent.mkdir(mode=0o700, exist_ok=True)
            with zf.open(info) as src:
                total += _copy_capped(src, dst, MAX_MEMBER_BYTES)
            if total > MAX_TOTAL_BYTES:
                raise IntakeError("the zip is too large once unpacked")
            copied += 1
        return copied


def receive(stream: BinaryIO, *, filename: str = "", max_bytes: int = MAX_UPLOAD_BYTES) -> Intake:
    """Take an uploaded export: a zip, or a bare conversations.json. Raises IntakeError (safe to show)."""
    intake_id, d = _new_dir()
    try:
        raw = d / "upload"
        _copy_capped(stream, raw, max_bytes)
        export_dir = d / "export"
        if zipfile.is_zipfile(raw):
            copied = _from_zip(raw, export_dir)
            if not copied:
                raise IntakeError("the zip holds none of the files an export has "
                                  "(conversations.json, SOUL.md, MEMORY.md)")
        elif filename.lower().endswith(".json") or not filename:
            shutil.copyfile(raw, export_dir / "conversations.json")
            os.chmod(export_dir / "conversations.json", 0o600)
        else:
            raise IntakeError("send a ChatGPT conversations.json or a zip of an export")
        _wipe_file(raw)
        logger.info("soul intake %s: received", intake_id)
        return Intake(intake_id, export_dir)
    except Exception:
        _wipe_tree(d)
        raise


# ── The dashboard's upload (P2.21.13, PR 4; Hub's conditions) ────────────────────────────────────
# The gateway streams the browser's file to <dir>/windy-soul-upload-<32 hex> (0600, O_EXCL), with <dir>
# $XDG_RUNTIME_DIR first and /dev/shm second, then hands the brain only that path. The brain takes it
# only when the name and the folder are right, opens it with O_NOFOLLOW and checks the OPEN descriptor
# (a regular file owned by the agent), so a file swapped for a link is never read. It wipes it after.

UPLOAD_PREFIX = "windy-soul-upload-"
_UPLOAD_NAME = re.compile(r"windy-soul-upload-[0-9a-f]{32}")
DASHBOARD_MAX_BYTES = 90 * 1024 * 1024     # under common edge-proxy request limits, so the 413 is ours


def upload_dirs() -> list[Path]:
    """Where the gateway may write an upload, in its order of preference."""
    out: list[Path] = []
    for cand in (os.environ.get("XDG_RUNTIME_DIR") or "", "/dev/shm"):
        if cand and Path(cand).is_dir():
            real = Path(os.path.realpath(cand))
            if real not in out:
                out.append(real)
    return out


def receive_upload(path: str, *, kind: str, max_bytes: int = DASHBOARD_MAX_BYTES) -> Intake:
    """Take the gateway's upload file and wipe it. ``kind`` ('zip' or 'json') is only a hint."""
    p = Path(path)
    if not _UPLOAD_NAME.fullmatch(p.name):
        raise IntakeError("that is not an upload from the dashboard")
    folder = Path(os.path.realpath(p.parent))
    if folder not in upload_dirs():
        raise IntakeError("that is not an upload from the dashboard")
    target = folder / p.name
    try:
        fd = os.open(target, os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_NOCTTY", 0))
    except OSError as e:
        raise IntakeError("the upload is gone or is not a plain file") from e
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
        os.close(fd)                       # not ours to read, and never ours to overwrite
        raise IntakeError("the upload is gone or is not a plain file")
    try:
        if st.st_size > max_bytes:
            raise IntakeError(f"the file is larger than {max_bytes // (1024 * 1024)} MB")
        with os.fdopen(os.dup(fd), "rb") as f:
            return receive(f, filename="export.zip" if kind == "zip" else "conversations.json",
                           max_bytes=max_bytes)
    finally:
        _wipe_fd(fd, st.st_size)
        os.close(fd)
        try:  # remove the name only while it is still this same file
            now = os.stat(target, follow_symlinks=False)
            if (now.st_dev, now.st_ino) == (st.st_dev, st.st_ino):
                target.unlink()
        except OSError:
            pass


def _wipe_fd(fd: int, size: int) -> None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        while size > 0:
            n = min(size, _CHUNK)
            os.write(fd, b"\0" * n)
            size -= n
        os.fsync(fd)
    except OSError:
        pass


def wipe_upload(path: str) -> None:
    """Wipe a gateway upload the brain did not take (a refusal before receive_upload ran)."""
    p = Path(path)
    if _UPLOAD_NAME.fullmatch(p.name) and Path(os.path.realpath(p.parent)) in upload_dirs():
        try:
            st = os.lstat(p)
            if stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid():
                _wipe_file(p)
        except OSError:
            pass


def sweep_uploads(max_age_s: float = MAX_AGE_S, dirs: list[Path] | None = None) -> int:
    """Remove dashboard uploads older than ``max_age_s`` (a gateway or brain stopped mid-handoff)."""
    removed, now = 0, time.time()
    for d in dirs if dirs is not None else upload_dirs():
        for f in d.glob(f"{UPLOAD_PREFIX}*"):
            try:
                st = f.lstat()
                if now - st.st_mtime <= max_age_s:
                    continue
                if stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid():
                    _wipe_file(f)
                elif stat.S_ISLNK(st.st_mode):
                    f.unlink()                     # never follow it
                else:
                    continue
                removed += 1
            except OSError:
                continue
    if removed:
        logger.info("soul intake: swept %d old dashboard upload(s)", removed)
    return removed


def discard(intake: Intake | None) -> None:
    """Overwrite and remove one intake (after approve, reject or a timeout)."""
    if intake is not None:
        _wipe_tree(intake.dir.parent)


def sweep(max_age_s: float = MAX_AGE_S, base: Path | None = None) -> int:
    """Remove intake folders older than ``max_age_s`` (an import cut short by a restart). Returns how many."""
    base = base or _base_dir()
    removed, now = 0, time.time()
    for d in base.glob(f"{_PREFIX}*"):
        try:
            if d.is_dir() and not d.is_symlink() and now - d.stat().st_mtime > max_age_s:
                _wipe_tree(d)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("soul intake: swept %d old intake folder(s)", removed)
    return removed


def _wipe_file(p: Path) -> None:
    try:
        size = p.stat().st_size
        with open(p, "r+b") as f:
            while size > 0:
                n = min(size, _CHUNK)
                f.write(b"\0" * n)
                size -= n
        p.unlink()
    except OSError:
        pass


def _wipe_tree(d: Path) -> None:
    if not d.exists() or d.is_symlink():
        return
    for p in sorted(d.rglob("*"), reverse=True):
        if p.is_symlink():
            p.unlink(missing_ok=True)
        elif p.is_file():
            _wipe_file(p)
        elif p.is_dir():
            p.rmdir()
    d.rmdir()
