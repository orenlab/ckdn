# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Writing ckdn's own documents without trusting what sits at their path.

A run directory is shared with the check's process tree, which knows its path
(``{run_dir}``) and can plant a symlink or a half-written file where ckdn is
about to write. Every document ckdn publishes is therefore written to a fresh
scratch file and renamed into place: the rename replaces whatever was at the
path -- a symlink included -- instead of writing through it, and a reader
sees either the previous document or the complete new one, never a torn one.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import sys
import time
from pathlib import Path

#: Windows refuses to rename over a file another process has open. A reader
#: (``ckdn show``, the MCP server) holds a digest only for a moment, so the
#: rename is retried briefly before giving up on atomicity.
_REPLACE_ATTEMPTS = 20
_REPLACE_PAUSE_SECONDS = 0.05


def _replace(scratch: Path, target: Path) -> None:
    if sys.platform != "win32":
        os.replace(scratch, target)
        return
    for _ in range(_REPLACE_ATTEMPTS - 1):  # pragma: no cover - Windows CI
        try:
            os.replace(scratch, target)
        except PermissionError:
            time.sleep(_REPLACE_PAUSE_SECONDS)
        else:
            return
    os.replace(scratch, target)  # pragma: no cover - Windows CI


def write_atomic(path: Path, data: bytes, *, durable: bool = True) -> None:
    """Publish ``data`` at ``path`` by rename; never follow a link there.

    ``durable`` flushes the bytes to disk before the rename, so a crash cannot
    leave an empty or truncated document behind the final name -- which for
    ``digest.json`` would mark a run finished that ``prune`` may then delete.
    """
    scratch = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    # O_EXCL refuses an existing entry, a planted symlink included.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(scratch, flags, 0o666)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            if durable:
                fh.flush()
                os.fsync(fh.fileno())
        _replace(scratch, path)
    except BaseException:
        with contextlib.suppress(OSError):
            scratch.unlink()
        raise


def write_text_atomic(path: Path, text: str) -> None:
    """:func:`write_atomic` for a UTF-8 document."""
    write_atomic(path, text.encode("utf-8"))
