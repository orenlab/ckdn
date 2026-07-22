# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Process execution and run-directory lifecycle.

The runner is the single component that owns the true exit code. It executes
the check command without a shell (tokens via ``shlex``), streams stdout and
stderr interleaved **straight into** ``full.log``, and never lets an exception
escape without producing a run directory -- a digest must exist for every
attempt.

Process-lifecycle rules (a hung child must never hang ckdn):

* The log is a file, never a pipe. A pipe's write end is inherited by every
  descendant, so draining it blocks until *all* of them exit -- killing the
  direct child is not enough and the parent deadlocks. Writing to a file also
  means an interrupted run still leaves partial evidence on disk.
* The child starts in its own process group/session, so the whole tree
  (``uv`` -> ``pytest`` -> workers) can be signalled as a unit.
* On timeout, on ``SIGINT``, and on any other exception the group is
  terminated: ``SIGTERM`` -> grace period -> ``SIGKILL``. Nothing is left
  running behind ckdn.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO

LOG_NAME = "full.log"
LATEST_LINK = "latest"
LATEST_FILE = "LATEST"  # fallback pointer where symlinks are unavailable

RC_TIMEOUT = 124
RC_NOT_FOUND = 127
RC_POLICY = 126
RC_INTERRUPTED = 130  # 128 + SIGINT, the conventional Ctrl-C exit code

#: Seconds a terminated process group gets to exit before it is killed.
TERM_GRACE_SECONDS = 5.0


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
    #: Why the process ended, alongside ``timed_out`` -- not a result of its
    #: own. True when the run was cut short by SIGINT.
    interrupted: bool = False


def terminate_tree(
    proc: subprocess.Popen[bytes], grace: float = TERM_GRACE_SECONDS
) -> None:
    """Terminate the child's whole process group: TERM, grace, then KILL.

    Safe to call on an already-exited process. Never raises: a tree we cannot
    signal must not mask the run's real outcome.
    """
    if proc.poll() is not None:
        return

    if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=grace)
        return

    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError):
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGTERM)
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=grace)


class RunLockError(RuntimeError):
    """Another live process already runs this check in this runs directory."""


def _pid_alive_windows(pid: int) -> bool:  # pragma: no cover - Windows CI
    """Ask the kernel directly; ``os.kill(pid, 0)`` is not usable here.

    On Windows a dead pid makes ``os.kill(pid, 0)`` raise a bare ``OSError``
    (``WinError 87``), indistinguishable from a real error -- treating that as
    "alive" would make a stale lock permanent.
    """
    if sys.platform != "win32":
        # Unreachable at runtime (guarded by the caller); the check narrows the
        # platform so type checkers on POSIX do not read `ctypes.WinDLL`.
        return False

    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(
        process_query_limited_information, False, wintypes.DWORD(pid)
    )
    if not handle:
        return False  # gone, or never existed
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        # A process that really exits with 259 is misread as alive; that is the
        # accepted ambiguity of this API, and it only delays reclaiming a lock.
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":  # pragma: no cover - Windows CI
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, simply not ours to signal
    return True


def _lock_path(runs_dir: Path, check: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in check)
    return runs_dir / ".locks" / f"{safe}.lock"


@contextlib.contextmanager
def run_lock(runs_dir: Path, check: str) -> Iterator[None]:
    """Hold an exclusive lock for ``check``; refuse a second concurrent run.

    Two runs of the same check in one workspace fight over the same tools and
    double the machine load, which is exactly how a hung run gets compounded.
    A lock left behind by a dead process is reclaimed.
    """
    path = _lock_path(runs_dir, check)
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in (1, 2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder: int | None = None
            with contextlib.suppress(OSError, ValueError):
                holder = int(path.read_text(encoding="utf-8").strip() or 0)
            if holder is not None and holder != os.getpid() and _pid_alive(holder):
                raise RunLockError(
                    f"check '{check}' is already running in this workspace "
                    f"(pid {holder}); wait for it or stop it before retrying"
                ) from None
            if attempt == 2:
                raise RunLockError(
                    f"could not acquire the run lock for '{check}': {path}"
                ) from None
            with contextlib.suppress(OSError):  # stale lock -> reclaim
                path.unlink()
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
            break
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            path.unlink()


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


@dataclass(frozen=True)
class _Ending:
    """How a child process finished, before it becomes a RunOutcome."""

    rc: int
    timed_out: bool = False
    interrupted: bool = False
    note: str | None = None


def _spawn(
    tokens: list[str],
    cwd: Path,
    log_fh: IO[bytes],
    run_env: dict[str, str] | None,
) -> subprocess.Popen[bytes]:
    """Start the child in its own process group, logging straight to a file."""
    if sys.platform == "win32":  # pragma: no cover - Windows CI
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        creationflags = 0
    return subprocess.Popen(
        tokens,
        cwd=cwd,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        env=run_env,
        start_new_session=sys.platform != "win32",
        creationflags=creationflags,
    )


def _await_child(proc: subprocess.Popen[bytes], timeout: float | None) -> _Ending:
    """Wait for the child; on timeout or SIGINT kill its whole group."""
    try:
        return _Ending(rc=proc.wait(timeout=timeout))
    except subprocess.TimeoutExpired:
        terminate_tree(proc)
        return _Ending(
            rc=RC_TIMEOUT,
            timed_out=True,
            note=f"command timed out after {timeout}s; process tree terminated",
        )
    except KeyboardInterrupt:
        # Swallow deliberately: the run must still produce evidence and a real
        # exit code (130) instead of a bare traceback.
        terminate_tree(proc)
        return _Ending(
            rc=RC_INTERRUPTED,
            interrupted=True,
            note="command interrupted by SIGINT; process tree terminated",
        )


def execute(
    tokens: list[str],
    cwd: Path,
    run_dir: Path,
    timeout: float | None,
    env: dict[str, str] | None = None,
) -> RunOutcome:
    started_at = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    t0 = time.monotonic()
    # Overlay per-check env on the inherited environment (keeps PATH etc.);
    # None means "inherit unchanged".
    run_env = {**os.environ, **env} if env else None
    log_path = run_dir / LOG_NAME
    ending = _Ending(rc=RC_NOT_FOUND)
    proc: subprocess.Popen[bytes] | None = None

    try:
        # Stream to the log file: no pipe means no descendant can hold the
        # parent hostage, and partial output survives an interrupt.
        with log_path.open("wb") as log_fh:
            try:
                proc = _spawn(tokens, cwd, log_fh, run_env)
            except FileNotFoundError as exc:
                ending = _Ending(
                    rc=RC_NOT_FOUND, note=f"command not found: {exc.filename}"
                )
            except OSError as exc:
                ending = _Ending(
                    rc=RC_NOT_FOUND, note=f"failed to start command: {exc}"
                )
            else:
                ending = _await_child(proc, timeout)
    finally:
        # Belt and braces: nothing may outlive this call on any path.
        if proc is not None and proc.poll() is None:
            terminate_tree(proc)

    log_text = (
        log_path.read_text(encoding="utf-8", errors="replace")
        if log_path.exists()
        else ""
    )

    return RunOutcome(
        run_dir=run_dir,
        tokens=tokens,
        rc=ending.rc,
        log_text=log_text,
        started_at=started_at,
        duration_s=round(time.monotonic() - t0, 3),
        timed_out=ending.timed_out,
        exec_note=ending.note,
        interrupted=ending.interrupted,
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
    if name.startswith("."):
        return None  # bookkeeping (.locks), not a run
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
    """Run directories only.

    Dot-prefixed entries are bookkeeping (``.locks``), never runs: they must
    not be listed, resolved, or pruned as if they held a digest.
    """
    if not runs_dir.is_dir():
        return []
    return sorted(
        p
        for p in runs_dir.iterdir()
        if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")
    )


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
