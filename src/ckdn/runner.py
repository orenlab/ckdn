# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Process execution and run-directory lifecycle.

The runner is the single component that owns the true exit code. It executes
the check command without a shell (tokens via ``shlex``), captures stdout and
stderr interleaved into ``full.log``, and never lets an exception escape
without producing a run directory -- a digest must exist for every attempt.
"""

from __future__ import annotations

import datetime as dt
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

LOG_NAME = "full.log"
LATEST_LINK = "latest"
LATEST_FILE = "LATEST"  # fallback pointer where symlinks are unavailable

RC_TIMEOUT = 124
RC_NOT_FOUND = 127


@dataclass(frozen=True)
class RunOutcome:
    run_dir: Path
    tokens: list[str]
    rc: int
    log_text: str
    started_at: str
    duration_s: float
    timed_out: bool
    exec_note: str | None


def create_run_dir(runs_dir: Path, check: str) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    base = f"{stamp}-{check}"
    candidate = runs_dir / base
    n = 1
    while candidate.exists():
        n += 1
        candidate = runs_dir / f"{base}-{n}"
    candidate.mkdir()
    return candidate


def build_tokens(command: str, run_dir: Path, extra: list[str]) -> list[str]:
    tokens = shlex.split(command) + list(extra)
    return [t.replace("{run_dir}", str(run_dir)) for t in tokens]


def execute(
    tokens: list[str],
    cwd: Path,
    run_dir: Path,
    timeout: float | None,
) -> RunOutcome:
    started_at = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    t0 = time.monotonic()
    timed_out = False
    note: str | None = None
    raw: bytes = b""

    try:
        proc = subprocess.run(
            tokens,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        rc = proc.returncode
        raw = proc.stdout or b""
    except subprocess.TimeoutExpired as exc:
        rc = RC_TIMEOUT
        out = exc.output
        raw = out if isinstance(out, bytes) else (out or "").encode()
        timed_out = True
        note = f"command timed out after {timeout}s"
    except FileNotFoundError as exc:
        rc = RC_NOT_FOUND
        note = f"command not found: {exc.filename}"
    except OSError as exc:
        rc = RC_NOT_FOUND
        note = f"failed to start command: {exc}"

    log_text = raw.decode("utf-8", errors="replace")
    (run_dir / LOG_NAME).write_text(log_text, encoding="utf-8")

    return RunOutcome(
        run_dir=run_dir,
        tokens=tokens,
        rc=rc,
        log_text=log_text,
        started_at=started_at,
        duration_s=round(time.monotonic() - t0, 3),
        timed_out=timed_out,
        exec_note=note,
    )


def update_latest(runs_dir: Path, run_dir: Path) -> None:
    """Point ``latest`` at ``run_dir``; fall back to a marker file on
    filesystems/platforms where symlinks are unavailable."""
    link = runs_dir / LATEST_LINK
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(run_dir.name, target_is_directory=True)
    except OSError:
        (runs_dir / LATEST_FILE).write_text(run_dir.name + "\n", encoding="utf-8")


def _contained_run(runs_dir: Path, name: str) -> Path | None:
    """Resolve ``name`` to a real directory strictly inside ``runs_dir``.

    A run id must be a single, non-traversing path segment naming a real
    directory (not a symlink) that resolves under ``runs_dir``. Anything else
    -- absolute paths, ``..``/``.``, multi-segment names, symlinked entries, or
    targets that escape the runs root -- resolves to ``None`` so the caller
    reports it as "no matching run" rather than reading outside the boundary.
    """
    if not name:
        return None
    ref_path = Path(name)
    if ref_path.is_absolute() or len(ref_path.parts) != 1 or name in {".", ".."}:
        return None
    candidate = runs_dir / name
    if candidate.is_symlink() or not candidate.is_dir():
        return None
    if not candidate.resolve().is_relative_to(runs_dir.resolve()):
        return None
    return candidate


def resolve_run_dir(runs_dir: Path, ref: str | None = None) -> Path | None:
    """Resolve a run reference (directory name or None for latest).

    Returns ``None`` for missing runs and for any reference that would escape
    ``runs_dir`` (see :func:`_contained_run`); the read-side maps that to a
    ``RunNotFoundError`` / MCP ``isError``.
    """
    if ref:
        return _contained_run(runs_dir, ref)
    link = runs_dir / LATEST_LINK
    if link.is_dir():
        target = link.resolve()
        if target.is_relative_to(runs_dir.resolve()):
            return target
        return None
    marker = runs_dir / LATEST_FILE
    if marker.is_file():
        return _contained_run(runs_dir, marker.read_text(encoding="utf-8").strip())
    return None


def list_run_dirs(runs_dir: Path) -> list[Path]:
    if not runs_dir.is_dir():
        return []
    return sorted(p for p in runs_dir.iterdir() if p.is_dir() and not p.is_symlink())


def prune(runs_dir: Path, keep: int) -> int:
    """Remove oldest run directories beyond ``keep``. Returns count removed."""
    if keep <= 0:
        return 0
    dirs = list_run_dirs(runs_dir)
    removed = 0
    for old in dirs[: max(0, len(dirs) - keep)]:
        shutil.rmtree(old, ignore_errors=True)
        removed += 1
    return removed
