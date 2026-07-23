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
* The child is detached into its own process group (POSIX) or held in a job
  object (Windows), so the whole tree (``uv`` -> ``pytest`` -> workers) can be
  stopped as a unit even after the wrapper that started it has exited.
* On timeout, on ``SIGINT``, on a clean exit and on any other path the tree is
  asked to stop, given a grace period, then terminated. What survives that is
  documented in ``docs/status-model.md`` rather than assumed away.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
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
from typing import IO, Any

LOG_NAME = "full.log"
#: A run directory without this file has not finished writing itself.
DIGEST_NAME = "digest.json"
LATEST_LINK = "latest"
LATEST_FILE = "LATEST"  # fallback pointer where symlinks are unavailable

RC_TIMEOUT = 124
RC_NOT_FOUND = 127
RC_POLICY = 126
RC_INTERRUPTED = 130  # 128 + SIGINT, the conventional Ctrl-C exit code

#: Seconds a terminated process group gets to exit before it is killed.
TERM_GRACE_SECONDS = 5.0
#: How often a wait wakes up to notice Ctrl-C or a dead process group.
POLL_SECONDS = 0.05
EMPTY_LOG_SHA256 = hashlib.sha256(b"").hexdigest()


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
    #: Taken over the bytes of ``full.log`` itself, so an external
    #: ``sha256 full.log`` matches. ``log_text`` is a lossy view of them:
    #: decoding replaces invalid bytes and collapses CRLF, which any Windows
    #: tool emits.
    log_sha256: str = EMPTY_LOG_SHA256
    log_size: int = 0


def _group_alive(pgid: int) -> bool:
    """True while *any* process remains in the group."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, simply not ours to signal
    return True


def _signal_group(pgid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, sig)


def _terminate_group(proc: subprocess.Popen[bytes], pgid: int, grace: float) -> None:
    """SIGTERM the group, wait out the grace, then SIGKILL whatever is left.

    The escalation watches the *group*, never the direct child. A wrapper
    (``uv``, ``sh``) dies on the first SIGTERM within milliseconds while the
    tool it launched ignores it -- waiting on the child would come back
    "finished" and the SIGKILL would never be sent, leaving exactly the
    orphans this mechanism exists to prevent.

    The poll reaps our own child as it goes. An unreaped zombie is still a
    member of the group, so leaving it would make the group look alive for the
    entire grace period: every interrupt would cost the full five seconds and
    end in SIGKILL, and no tool would ever get the chance to shut down cleanly
    that the grace period exists to give it.
    """
    _signal_group(pgid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        proc.poll()
        if not _group_alive(pgid):
            return
        time.sleep(POLL_SECONDS)
    if _group_alive(pgid):
        _signal_group(pgid, signal.SIGKILL)


def _terminate_tree_posix(proc: subprocess.Popen[bytes], grace: float) -> None:
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError):
        # The child is already reaped. It led its own session by construction,
        # so its pid is the group id -- and POSIX reserves that id while the
        # group still has members, which is precisely the case worth signalling.
        pgid = proc.pid
    if pgid == os.getpgid(0):
        # start_new_session did not take effect: this is ckdn's own group and
        # signalling it would kill ckdn. Settle for the direct child.
        with contextlib.suppress(OSError):
            proc.kill()
        return
    if not _group_alive(pgid):
        return  # nothing left; do not risk signalling a recycled group id
    _terminate_group(proc, pgid, grace)


def _win32() -> Any:
    """The Win32 layer, imported only where its own imports resolve."""
    if sys.platform != "win32":  # pragma: no cover - POSIX
        return None

    from ckdn import _win32 as module

    return module


def terminate_tree(
    proc: subprocess.Popen[bytes], grace: float = TERM_GRACE_SECONDS
) -> None:
    """Terminate every process the run started, not just the direct child.

    Safe to call whether or not the child is still alive -- descendants
    routinely outlive it. Never raises: a tree we cannot signal must not mask
    the run's real outcome.
    """
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI
        _win32().terminate_tree(proc, grace, POLL_SECONDS)
    else:
        _terminate_tree_posix(proc, grace)
    with contextlib.suppress(subprocess.TimeoutExpired, OSError, ValueError):
        proc.wait(timeout=grace)


class RunLockError(RuntimeError):
    """Another live process already runs this check in this runs directory."""


def _lock_path(runs_dir: Path, check: str) -> Path:
    """One lock per check name, and never one lock for two of them.

    Sanitizing alone is not injective: every unsafe character becomes ``_``,
    so ``py.test`` and ``py_test`` collided on a single lock and each would
    refuse to run while the *other* was in flight — and report the other as a
    run that "did not exit cleanly". The readable part stays for humans; the
    digest is what makes the name unique.
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in check)
    tag = hashlib.sha256(check.encode("utf-8")).hexdigest()[:8]
    return runs_dir / ".locks" / f"{safe}-{tag}.lock"


def _try_lock(fd: int) -> bool:
    """Take an exclusive advisory lock without blocking; ``False`` if held.

    The kernel arbitrates, so there is no window between testing and taking
    the lock and no pid left to interpret. The previous protocol (create the
    file exclusively, then write a pid, then judge that pid's liveness) could
    hand the same check to two runs three different ways, and could not see a
    second *thread* of ckdn's own process at all -- which is exactly how the
    MCP server runs checks.
    """
    if sys.platform == "win32":  # pragma: no cover - Windows CI
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _stale_lock_note(holder: str) -> str:
    """Say what an unreleased lock proves -- and nothing beyond it.

    Holding the lock means no other run has it now; a non-empty file means the
    run that held it never reached its own cleanup. Whether it was killed,
    crashed, or the machine rebooted is unknowable here, and so is whether
    anything it started survived. The recorded pid is ckdn's own, never the
    child's process group, so this names no target to kill and ckdn stops
    nothing on its own.
    """
    return (
        f"the previous run of this check ({holder}) did not exit cleanly, so "
        "processes it started may still be running. ckdn does not stop them "
        "automatically -- check for leftovers if this run behaves oddly"
    )


@contextlib.contextmanager
def run_lock(runs_dir: Path, check: str) -> Iterator[str | None]:
    """Hold an exclusive lock for ``check``; refuse a second concurrent run.

    Two runs of the same check in one workspace fight over the same tools and
    double the machine load, which is exactly how a hung run gets compounded.
    Yields a note when the previous holder died without releasing, else
    ``None``.

    The lock is the file *descriptor*, not the file: it is released by the
    kernel however ckdn dies, so nothing has to be reclaimed and no unlink can
    fail. The file's contents are only a record of who last held it.
    """
    path = _lock_path(runs_dir, check)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        if not _try_lock(fd):
            raise RunLockError(
                f"check '{check}' is already running in this workspace; "
                "wait for it or stop it before retrying"
            )
        held_by = os.read(fd, 128).decode("utf-8", errors="replace").strip()
        os.lseek(fd, 0, os.SEEK_SET)
        os.truncate(fd, 0)
        os.write(fd, f"ckdn pid {os.getpid()}".encode())
        try:
            yield _stale_lock_note(held_by) if held_by else None
        finally:
            # Emptying the file is the "released cleanly" marker. The lock
            # itself goes with the descriptor, so a run that is killed leaves
            # the record behind and the next one can say so.
            with contextlib.suppress(OSError):
                os.truncate(fd, 0)
    finally:
        os.close(fd)


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
    proc = subprocess.Popen(
        tokens,
        cwd=cwd,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        env=run_env,
        start_new_session=sys.platform != "win32",
        creationflags=creationflags,
    )
    return proc


def _wait_interruptibly(proc: subprocess.Popen[bytes], timeout: float | None) -> int:
    """Wait in short slices so Ctrl-C is actually delivered.

    ``Popen.wait(None)`` blocks in ``WaitForSingleObject(INFINITE)`` on
    Windows, where a Ctrl-C only becomes a ``KeyboardInterrupt`` between
    bytecodes -- an unbounded wait there swallows the interrupt entirely and
    the check cannot be stopped at all. Polling costs one wakeup per 50ms and
    behaves identically on every OS.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        rc = proc.poll()
        if rc is not None:
            return rc
        if deadline is not None and time.monotonic() >= deadline:
            # `deadline` is set only when a timeout was given, so it is a float.
            raise subprocess.TimeoutExpired(proc.args, timeout or 0.0)
        time.sleep(POLL_SECONDS)


def _terminate_absorbing_interrupts(proc: subprocess.Popen[bytes]) -> None:
    """Terminate the tree; an impatient second Ctrl-C must not escape.

    Five seconds of silence invites another keypress, and letting that one
    through would abandon the run without a digest -- the empty run directory
    from the original incident.
    """
    try:
        terminate_tree(proc)
    except KeyboardInterrupt:
        # Second Ctrl-C: drop the grace period and take what evidence exists.
        with contextlib.suppress(KeyboardInterrupt):
            terminate_tree(proc, grace=0.0)


def _await_child(proc: subprocess.Popen[bytes], timeout: float | None) -> _Ending:
    """Wait for the child; on timeout or SIGINT kill its whole group."""
    try:
        return _Ending(rc=_wait_interruptibly(proc, timeout))
    except subprocess.TimeoutExpired:
        _terminate_absorbing_interrupts(proc)
        return _Ending(
            rc=RC_TIMEOUT,
            timed_out=True,
            note=f"command timed out after {timeout}s; process tree terminated",
        )
    except KeyboardInterrupt:
        # Swallow deliberately: the run must still produce evidence and a real
        # exit code (130) instead of a bare traceback.
        _terminate_absorbing_interrupts(proc)
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
                if sys.platform == "win32":  # pragma: no cover - Windows CI
                    _win32().attach_job(proc)
                ending = _await_child(proc, timeout)
    except KeyboardInterrupt:
        # Ctrl-C outside the wait -- while the log file is opened, or while
        # the child is being put in its job. The `finally` below still stops
        # the tree, and the run still reports an interrupt with its evidence
        # rather than escaping with no outcome at all.
        ending = _Ending(
            rc=RC_INTERRUPTED,
            interrupted=True,
            note="run interrupted while starting the command",
        )
    finally:
        # On every path, including a clean exit: a check that leaves a
        # background process behind would otherwise keep writing into a log
        # whose digest is already sealed.
        if proc is not None:
            _terminate_absorbing_interrupts(proc)

    log_bytes = log_path.read_bytes() if log_path.exists() else b""
    log_text = log_bytes.decode("utf-8", errors="replace")

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
        log_sha256=hashlib.sha256(log_bytes).hexdigest(),
        log_size=len(log_bytes),
    )


def _publish_symlink(runs_dir: Path, run_dir: Path) -> bool:
    """Swap ``latest`` into place by rename; False if symlinks are unusable."""
    scratch = runs_dir / f".{LATEST_LINK}.{os.getpid()}.tmp"
    try:
        scratch.symlink_to(run_dir.name, target_is_directory=True)
        os.replace(scratch, runs_dir / LATEST_LINK)
    except OSError:
        with contextlib.suppress(OSError):
            scratch.unlink()
        return False
    # Drop a marker left by an earlier fallback: beside a working link it
    # contradicts it, and readers cannot tell which pointer is newer.
    marker = runs_dir / LATEST_FILE
    if marker.is_file():
        with contextlib.suppress(OSError):
            marker.unlink()
    return True


def update_latest(runs_dir: Path, run_dir: Path) -> None:
    """Point ``latest`` at ``run_dir``, atomically.

    Falls back to a marker file where symlinks are unavailable. Both forms are
    published by rename: unlinking first left a window with no pointer at all,
    and two runs finishing together could both unlink and then race to create
    — the loser failing over to the marker, leaving two pointers that
    disagreed about which run was latest.
    """
    # Windows gets the marker unconditionally. Symlink creation needs
    # privilege there, `os.replace` cannot replace a *directory* (and a run-dir
    # symlink is one), and `LATEST` and `latest` are a single path on a
    # case-insensitive filesystem -- publishing both forms collided with
    # itself.
    if sys.platform != "win32" and _publish_symlink(runs_dir, run_dir):
        return
    scratch = runs_dir / f".{LATEST_FILE}.{os.getpid()}.tmp"
    scratch.write_text(run_dir.name + "\n", encoding="utf-8")
    os.replace(scratch, runs_dir / LATEST_FILE)


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
    """Remove oldest *finished* run directories beyond ``keep``.

    A run without a digest has not finished writing itself. Pruning is global
    while the run lock is per check, so a fast check retiring old runs would
    otherwise delete a slow check's directory out from under it -- the log
    vanishes mid-write and the victim never produces a digest at all. Returns
    the count removed.
    """
    if keep <= 0:
        return 0
    finished = [d for d in list_run_dirs(runs_dir) if (d / DIGEST_NAME).exists()]
    removed = 0
    for old in finished[: max(0, len(finished) - keep)]:
        shutil.rmtree(old, ignore_errors=True)
        removed += 1
    return removed
