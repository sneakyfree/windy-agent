"""Private export archives (windyfly 0.7.7: the private-export fix only)."""

from __future__ import annotations

import contextlib
import os
import tarfile
from collections.abc import Iterator


@contextlib.contextmanager
def private_tar(path: str | os.PathLike) -> Iterator[tarfile.TarFile]:
    """A new ``.tar.gz`` that only the owner's account can read (0600), whatever the umask.

    An export holds the whole memory AND the ``.env`` secrets; with the usual 0022 umask it came out 0644,
    readable by every account on the machine. The mode is set when the file is created (and again with
    fchmod, for a file that already existed), never after the secrets are in it. A symlink at ``path`` is
    refused (O_NOFOLLOW)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    if hasattr(os, "fchmod"):
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, "wb") as fh, tarfile.open(fileobj=fh, mode="w:gz") as tar:
        yield tar
