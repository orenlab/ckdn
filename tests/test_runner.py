# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Unit tests for process execution and run-directory lifecycle."""

from __future__ import annotations

import _thread
import contextlib
import datetime as dt
import hashlib
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ckdn.runner import (
    DIGEST_NAME,
    LATEST_FILE,
    LATEST_LINK,
    LOG_NAME,
    RC_INTERRUPTED,
    RC_NOT_FOUND,
    RC_TIMEOUT,
    TERM_GRACE_SECONDS,
    RunLockError,
    _lock_path,
    _spawn,
    build_tokens,
    create_run_dir,
    execute,
    list_run_dirs,
    prune,
    resolve_run_dir,
    run_lock,
    terminate_tree,
    update_latest,
)


def test_create_run_dir_collision_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "runs"
    fixed = dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt.UTC)

    class _Clock:
        @staticmethod
        def now(tz: dt.tzinfo | None = None) -> dt.datetime:
            return fixed

    monkeypatch.setattr("ckdn.runner.dt.datetime", _Clock)
    first = create_run_dir(runs, "frozen")
    assert first.name == "20260101T000000Z-frozen"
    second = create_run_dir(runs, "frozen")
    assert second.name == "20260101T000000Z-frozen-2"
    assert first.is_dir() and second.is_dir()


def test_build_tokens_substitutes_run_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / "r"
    tokens = build_tokens("echo {run_dir}/out", run_dir, ["--flag"])
    assert tokens == ["echo", f"{run_dir}/out", "--flag"]


def test_execute_success_writes_log(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outcome = execute([sys.executable, "-c", "print('hi')"], tmp_path, run_dir, None)
    assert outcome.rc == 0
    assert "hi" in outcome.log_text
    assert (run_dir / LOG_NAME).read_text(encoding="utf-8").startswith("hi")
    assert outcome.timed_out is False
    assert outcome.exec_note is None


def test_execute_timeout(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outcome = execute(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        tmp_path,
        run_dir,
        0.05,
    )
    assert outcome.rc == RC_TIMEOUT
    assert outcome.timed_out is True
    assert outcome.exec_note is not None
    assert "timed out" in outcome.exec_note


def test_execute_overlays_env_and_keeps_inherited(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    code = (
        "import os,sys; "
        "sys.stdout.write(os.environ.get('CKDN_X','MISSING')); "
        "sys.stdout.write('|' + ('PATH' in os.environ and 'has-path' or 'no-path'))"
    )
    outcome = execute(
        [sys.executable, "-c", code], tmp_path, run_dir, None, env={"CKDN_X": "hello"}
    )
    assert outcome.rc == 0
    # per-check var is injected, and the inherited environment survives
    assert outcome.log_text == "hello|has-path"


def test_execute_command_not_found(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outcome = execute(["ckdn-nonexistent-binary-xyz"], tmp_path, run_dir, None)
    assert outcome.rc == RC_NOT_FOUND
    assert outcome.exec_note is not None
    assert "not found" in outcome.exec_note


def test_execute_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    def _boom(*_a: object, **_k: object) -> object:
        raise OSError("permission denied")

    monkeypatch.setattr(subprocess, "Popen", _boom)
    outcome = execute(["true"], tmp_path, run_dir, None)
    assert outcome.rc == RC_NOT_FOUND
    assert outcome.exec_note is not None
    assert "failed to start" in outcome.exec_note


# --- process lifecycle (regression: the 2026-07 machine-hang incident) -----

# A child that spawns a grandchild and then sleeps. The grandchild inherits
# the child's stdout: with a pipe, draining it blocks until *every* holder
# exits, so killing only the direct child deadlocked the parent forever.
_SPAWNS_GRANDCHILD = (
    "import subprocess,sys,time;"
    "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
    "sys.stdout.write(str(p.pid));sys.stdout.flush();"
    "time.sleep(30)"
)


def _alive(pid: int) -> bool:
    """Portable liveness for the tests themselves.

    Production has no single-pid check on POSIX any more — termination is
    driven by the process group — so this lives here rather than being kept
    alive in the runner for the tests' benefit.
    """
    if sys.platform == "win32":
        from ckdn import _win32

        return _win32.pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, simply not ours to signal
    return True


def _wait_dead(pid: int, limit: float = 10.0) -> bool:
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_timeout_kills_the_whole_tree_and_never_hangs(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    start = time.monotonic()
    outcome = execute(
        [sys.executable, "-c", _SPAWNS_GRANDCHILD], tmp_path, run_dir, timeout=1.0
    )
    elapsed = time.monotonic() - start

    assert outcome.timed_out is True
    assert outcome.rc == RC_TIMEOUT
    # The bug: this call used to block forever on the inherited pipe.
    assert elapsed < 20, "execute() must not wait on orphaned descendants"
    # Evidence survives the timeout because the log streams to disk.
    grandchild = int(outcome.log_text.strip())
    assert _wait_dead(grandchild), "grandchild outlived the terminated group"


# The purest form of the deadlock: the direct child exits *immediately* and
# leaves a grandchild holding the inherited stdout. Waiting on the child is not
# enough — with a pipe, EOF only arrives when the grandchild goes too.
_ORPHAN_HOLDS_STDOUT = (
    "import subprocess,sys;"
    "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
    "sys.stdout.write(str(p.pid));sys.stdout.flush()"
)


def test_a_descendant_holding_stdout_cannot_block_the_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    start = time.monotonic()
    outcome = execute(
        [sys.executable, "-c", _ORPHAN_HOLDS_STDOUT], tmp_path, run_dir, 60
    )
    elapsed = time.monotonic() - start
    grandchild = int(outcome.log_text.strip())
    try:
        assert outcome.rc == 0
        assert outcome.timed_out is False
        # The bug: this returned only when the *grandchild* finally exited.
        assert elapsed < 10, "execute() waited on a descendant that outlived the child"
    finally:
        # SIGTERM, not SIGKILL: Windows has no SIGKILL, and os.kill there maps
        # any other signal to TerminateProcess.
        with contextlib.suppress(OSError):
            os.kill(grandchild, signal.SIGTERM)


def _interrupt_once_the_child_has_spoken(log: Path) -> None:
    """Interrupt the main thread as soon as the child has produced output.

    A fixed delay is a coin flip on a loaded runner: too short and the
    grandchild's pid is not in the log yet, too long and the suite drags for
    no reason. Waiting for the evidence itself is both faster and stable.
    """

    def _wait_then_interrupt() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if log.exists() and log.read_bytes().strip():
                break
            time.sleep(0.02)
        _thread.interrupt_main()

    threading.Thread(target=_wait_then_interrupt, daemon=True).start()


def test_interrupt_terminates_tree_records_evidence_and_rc_130(
    tmp_path: Path,
) -> None:
    """A real interrupt, not an injected one.

    ``_thread.interrupt_main`` raises ``KeyboardInterrupt`` in the main thread
    exactly the way a console Ctrl-C does — which is the point: an unbounded
    ``Popen.wait(None)`` never notices it on Windows, so a monkeypatched
    ``wait`` would pass here while the real thing ignored every keypress.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _interrupt_once_the_child_has_spoken(run_dir / LOG_NAME)
    outcome = execute(
        [sys.executable, "-c", _SPAWNS_GRANDCHILD], tmp_path, run_dir, None
    )

    assert outcome.interrupted is True and outcome.timed_out is False
    assert outcome.rc == RC_INTERRUPTED == 130
    assert "interrupted by SIGINT" in (outcome.exec_note or "")
    assert (run_dir / LOG_NAME).exists(), "evidence must exist after an interrupt"
    grandchild = int(outcome.log_text.strip())
    assert _wait_dead(grandchild), "grandchild outlived the interrupt"


@pytest.mark.skipif(
    os.name == "nt",
    reason="SIGTERM-immunity is POSIX signal semantics; the Windows "
    "escalation has its own tests",
)
def test_a_term_immune_grandchild_is_still_killed(tmp_path: Path) -> None:
    """The wrapper dies on SIGTERM; the tool it launched ignores it.

    Escalation used to watch the direct child, so the wrapper's prompt exit
    ended the wait and SIGKILL was never sent — while the digest claimed the
    process tree had been terminated.
    """
    child = (
        "import subprocess,sys,time;"
        "gc=subprocess.Popen([sys.executable,'-c',"
        '"import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"'
        '"time.sleep(120)"]);'
        "sys.stdout.write(str(gc.pid));sys.stdout.flush();time.sleep(120)"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outcome = execute([sys.executable, "-c", child], tmp_path, run_dir, 1.0)
    grandchild = int(outcome.log_text.strip())
    try:
        assert outcome.timed_out is True
        assert _wait_dead(grandchild), (
            "a SIGTERM-immune descendant survived, but the note said the tree "
            "was terminated"
        )
    finally:
        with contextlib.suppress(OSError):
            os.kill(grandchild, signal.SIGKILL)


def test_pid_alive_tells_a_live_process_from_a_dead_one() -> None:
    """Windows cannot use `os.kill(pid, 0)`; this is where that lives now."""
    if sys.platform != "win32":
        pytest.skip("the POSIX path has no single-pid liveness check")

    from ckdn import _win32

    live = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    dead = subprocess.Popen([sys.executable, "-c", ""])
    dead.wait()
    try:
        assert _win32.pid_alive(live.pid) is True
        assert _win32.pid_alive(dead.pid) is False
    finally:
        live.kill()
        live.wait()


_HOLD_LOCK = """
import os, pathlib, sys, time
sys.path.insert(0, sys.argv[1])
from ckdn.runner import run_lock
with run_lock(pathlib.Path(sys.argv[2]), sys.argv[3]):
    print(os.getpid(), flush=True)
    time.sleep(60)
"""


def _start_holder(tmp_path: Path, check: str) -> tuple[subprocess.Popen[str], int]:
    """Start a process that holds ``check``'s lock; return it and its real pid."""
    src = str(Path(__file__).resolve().parent.parent / "src")
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK, src, str(tmp_path), check],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    return holder, int(holder.stdout.readline().strip())


def test_run_lock_refuses_a_second_live_run(tmp_path: Path) -> None:
    """Another *process* holds it, which is what the lock is actually for."""
    holder, _ = _start_holder(tmp_path, "pytest")
    try:
        with (
            pytest.raises(RunLockError, match="already running"),
            run_lock(tmp_path, "pytest"),
        ):
            pass
    finally:
        holder.kill()
        holder.wait()


def test_run_lock_refuses_a_second_acquire_from_the_same_process(
    tmp_path: Path,
) -> None:
    """The MCP server runs sync tools on a thread pool.

    Judging the lock by "is that pid alive?" answered *yes, it is me* and
    handed the same check to both threads — the one case the lock exists to
    prevent that a pid can never detect.
    """
    with (
        run_lock(tmp_path, "same"),
        pytest.raises(RunLockError, match="already running"),
        run_lock(tmp_path, "same"),
    ):
        pass


def test_lock_dir_is_never_mistaken_for_a_run(tmp_path: Path) -> None:
    """`.locks` lives inside runs_dir; listing/pruning must ignore it."""
    runs = tmp_path / "runs"
    real = create_run_dir(runs, "a")
    with run_lock(runs, "a"):
        assert (runs / ".locks").is_dir()
        assert list_run_dirs(runs) == [real]
        assert resolve_run_dir(runs, ".locks") is None
        prune(runs, keep=1)
        assert (runs / ".locks").is_dir(), "prune must not delete bookkeeping"


def test_a_clean_release_leaves_no_warning_for_the_next_run(tmp_path: Path) -> None:
    """Two runs in a row: the second must not be told the first crashed.

    An unlink that failed (a Windows scanner holding the file is enough) used
    to leave the record behind and make every later run report a crash that
    never happened. The record is emptied instead, which cannot fail that way.
    """
    with run_lock(tmp_path, "twice") as first:
        assert first is None
    with run_lock(tmp_path, "twice") as second:
        assert second is None


def test_a_lock_never_released_warns_without_naming_a_target(
    tmp_path: Path,
) -> None:
    """The warning reports what happened and stops there.

    Only ckdn's own pid was ever recorded — never the child's process group —
    and a pid can be recycled, so the note must not point at anything to kill,
    and ckdn must kill nothing by itself.
    """
    lock = _lock_path(tmp_path, "z")
    lock.parent.mkdir(parents=True)
    lock.write_text("ckdn pid 4242", encoding="utf-8")  # killed before releasing

    with run_lock(tmp_path, "z") as note:
        assert note is not None
        assert "ckdn pid 4242" in note
        assert "did not exit cleanly" in note
        assert "may still be running" in note
        assert "does not stop them automatically" in note
        # No promise about a process group, and nothing framed as a kill target.
        assert "pgid" not in note.lower() and "process group" not in note.lower()


def test_a_clean_acquire_yields_no_warning(tmp_path: Path) -> None:
    with run_lock(tmp_path, "quiet") as note:
        assert note is None


def test_the_lock_empties_its_record_on_exit(tmp_path: Path) -> None:
    lock = _lock_path(tmp_path, "x")
    with run_lock(tmp_path, "x"):
        pass  # the record cannot be read from here: Windows locks are mandatory
    assert lock.read_text(encoding="utf-8") == "", (
        "an empty record is how the next run knows this one finished"
    )


def test_a_killed_run_is_named_in_the_next_runs_warning(tmp_path: Path) -> None:
    """End-to-end: hold the lock in another process, kill it, then acquire.

    This is the whole point of recording a holder — it proves the record
    survives a kill and identifies who left it, without reading the file
    while the lock is held.
    """
    holder, holder_pid = _start_holder(tmp_path, "named")
    holder.kill()
    holder.wait()

    with run_lock(tmp_path, "named") as note:
        assert note is not None
        assert f"ckdn pid {holder_pid}" in note


@pytest.mark.skipif(
    os.name == "nt",
    reason="symlink creation needs privilege on Windows; the "
    "marker fallback is covered by test_update_latest_fallback_marker",
)
def test_update_latest_symlink_and_resolve(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    run_dir = create_run_dir(runs, "a")
    update_latest(runs, run_dir)
    assert (runs / LATEST_LINK).exists()
    resolved = resolve_run_dir(runs)
    assert resolved is not None
    assert resolved.resolve() == run_dir.resolve()
    assert resolve_run_dir(runs, run_dir.name) == run_dir
    assert resolve_run_dir(runs, "missing") is None


def test_update_latest_fallback_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "runs"
    run_dir = create_run_dir(runs, "b")

    def _fail_symlink(self: Path, *_a: object, **_k: object) -> None:
        raise OSError("no symlinks")

    monkeypatch.setattr(Path, "symlink_to", _fail_symlink)
    update_latest(runs, run_dir)
    marker = runs / LATEST_FILE
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8").strip() == run_dir.name
    assert resolve_run_dir(runs) == run_dir


def test_resolve_run_dir_rejects_escape(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    real = create_run_dir(runs, "real")
    # A plain single-segment id still resolves.
    assert resolve_run_dir(runs, real.name) == real

    # Absolute paths, traversal, and multi-segment ids never escape runs_dir.
    outside = create_run_dir(tmp_path / "other", "victim")
    assert resolve_run_dir(runs, str(outside)) is None
    assert resolve_run_dir(runs, "..") is None
    assert resolve_run_dir(runs, ".") is None
    assert resolve_run_dir(runs, "../other/victim") is None
    assert resolve_run_dir(runs, f"sub/{real.name}") is None
    assert resolve_run_dir(runs, "") is None


def test_resolve_run_dir_rejects_symlinked_run_id(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    outside = create_run_dir(tmp_path / "other", "victim")
    evil = runs / "evil"
    try:
        evil.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    # list_run_dirs already skips symlinks; resolve_run_dir must too.
    assert evil.is_dir()  # dangling check: it does resolve to a dir
    assert resolve_run_dir(runs, "evil") is None
    assert evil not in list_run_dirs(runs)


def test_resolve_run_dir_marker_cannot_escape(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    outside = create_run_dir(tmp_path / "other", "victim")
    # A tampered LATEST marker pointing outside the runs root is refused.
    (runs / LATEST_FILE).write_text(f"../other/{outside.name}\n", encoding="utf-8")
    assert resolve_run_dir(runs) is None


def _finished(run_dir: Path) -> Path:
    (run_dir / DIGEST_NAME).write_text("{}", encoding="utf-8")
    return run_dir


def test_list_and_prune(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    assert list_run_dirs(runs) == []
    dirs = [_finished(create_run_dir(runs, f"c{i}")) for i in range(5)]
    assert len(list_run_dirs(runs)) == 5
    assert prune(runs, 0) == 0
    removed = prune(runs, 2)
    assert removed == 3
    assert list_run_dirs(runs) == dirs[-2:]


def test_prune_never_deletes_a_run_still_being_written(tmp_path: Path) -> None:
    """Pruning is global; the run lock is per check.

    A fast check retiring old runs would otherwise delete a slow check's
    directory mid-write — its log vanishes and it never produces a digest.
    """
    runs = tmp_path / "runs"
    old_finished = [_finished(create_run_dir(runs, f"old{i}")) for i in range(3)]
    in_flight = create_run_dir(runs, "slow")  # no digest yet: still running

    assert prune(runs, 1) == 2
    assert in_flight.is_dir(), "an unfinished run was pruned out from under it"
    assert old_finished[-1].is_dir()


def test_meta_hash_describes_the_bytes_that_are_on_disk(tmp_path: Path) -> None:
    """One CRLF used to be enough to make the log look tampered with.

    The hash was taken over `log_text`, and decoding collapses CRLF, so an
    external `sha256 full.log` disagreed with meta.json for the output of
    almost any Windows tool.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outcome = execute(
        [sys.executable, "-c", r"import sys;sys.stdout.buffer.write(b'a\r\nb')"],
        tmp_path,
        run_dir,
        30,
    )
    on_disk = (run_dir / LOG_NAME).read_bytes()
    assert on_disk == b"a\r\nb"
    assert outcome.log_sha256 == hashlib.sha256(on_disk).hexdigest()
    assert outcome.log_size == len(on_disk)


def test_two_checks_whose_names_sanitize_alike_get_separate_locks(
    tmp_path: Path,
) -> None:
    """`py.test` and `py_test` are different checks.

    Sanitizing every unsafe character to `_` made them share one lock: each
    refused to start while the *other* ran, and reported the other as a run
    that "did not exit cleanly".
    """
    with run_lock(tmp_path, "py.test"), run_lock(tmp_path, "py_test") as note:
        assert note is None, "the other check's record was read as this one's"


def test_latest_always_points_somewhere_while_being_updated(
    tmp_path: Path,
) -> None:
    """Publishing by rename leaves no window with no pointer at all."""
    runs = tmp_path / "runs"
    first = create_run_dir(runs, "a")
    update_latest(runs, first)
    assert resolve_run_dir(runs) is not None

    second = create_run_dir(runs, "b")
    update_latest(runs, second)
    resolved = resolve_run_dir(runs)
    assert resolved is not None
    assert resolved.resolve() == second.resolve()
    # Exactly one pointer form, never two that could disagree about which run
    # is newest. Which form it is depends on the platform — Windows publishes
    # the marker, POSIX the symlink — and on a case-insensitive filesystem the
    # two names are one path, so the link is identified by being a symlink
    # rather than by its name existing.
    assert (runs / LATEST_LINK).is_symlink() != (runs / LATEST_FILE).is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX zombie/process-group semantics")
def test_a_tool_that_exits_on_sigterm_is_not_killed_anyway(tmp_path: Path) -> None:
    """The grace period has to be able to end early, or it is not a grace.

    An unreaped child is still a member of its group, so `killpg(pgid, 0)`
    kept answering "alive" after it had already died: every timeout and every
    Ctrl-C burned the full five seconds and finished with SIGKILL, which is
    exactly the shutdown a tool is being given the grace period to avoid.
    Measured against codeclone's suite, a Ctrl-C took 5.24s.
    """
    log = tmp_path / "l"
    with log.open("wb") as fh:
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time;time.sleep(300)"],
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    time.sleep(0.3)  # let it reach the sleep before signalling
    start = time.monotonic()
    terminate_tree(proc)
    elapsed = time.monotonic() - start

    assert elapsed < TERM_GRACE_SECONDS / 2, (
        f"terminate_tree took {elapsed:.2f}s for a child that dies on SIGTERM; "
        "the grace period is being waited out in full"
    )
    assert proc.returncode is not None


def test_a_job_object_really_holds_and_kills_the_child() -> None:
    """The Win32 calls themselves work.

    Note this is the *mechanism*, not the wiring: see the seam test below for
    whether production actually reaches it.

    Every ctypes step falls back to `taskkill`, so a job that is never
    created passes every other test exactly like one that works. This
    exercises the real calls end to end: create, assign a live child, then
    drop the handle — KILL_ON_JOB_CLOSE has to take the child with it, which
    is also what makes a killed ckdn stop leaking its tree.
    """
    if sys.platform != "win32":
        # Guards rather than a marker: this also narrows the platform, so the
        # type checkers stop reading Windows-only names on POSIX.
        pytest.skip("Windows job objects")

    from ckdn import _win32  # imported here: its own imports need Windows

    job = _win32.create_job()
    assert job is not None, "CreateJobObject / SetInformationJobObject failed"

    child = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(60)"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    closed = False
    try:
        assert _win32.assign_job(job, child.pid) is True, (
            "AssignProcessToJobObject failed"
        )
        assert _win32.pid_alive(child.pid) is True
        _win32.close(job)  # the kill is the handle going away
        closed = True
        assert _wait_dead(child.pid), "KILL_ON_JOB_CLOSE did not take the child"
    finally:
        if not closed:  # a failed assertion above must not leak the job
            _win32.close(job)
        with contextlib.suppress(OSError):
            child.kill()
        child.wait()


def test_the_spawn_seam_puts_the_child_in_a_job_and_uses_it(tmp_path: Path) -> None:
    """The wiring, not the mechanism.

    The Win32 test above passes even if production never creates a job —
    every step falls back to `taskkill`, so deleting the `setattr` or the
    `_win_close` would leave the suite green. This drives the real seam:
    spawn, attach, terminate, and check that the job was both attached and
    handed back, and that a re-parented grandchild went with it.
    """
    if sys.platform != "win32":
        pytest.skip("Windows job objects")

    from ckdn import _win32

    log = tmp_path / "full.log"
    with log.open("wb") as handle:
        proc = _spawn(
            [sys.executable, "-c", _SPAWNS_GRANDCHILD], tmp_path, handle, None
        )
    _win32.attach_job(proc)
    try:
        assert getattr(proc, "_ckdn_win_job", None) is not None, (
            "the spawn seam did not put the child in a job"
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not log.read_bytes().strip():
            time.sleep(0.05)
        grandchild = int(log.read_text().strip())

        terminate_tree(proc)

        assert not hasattr(proc, "_ckdn_win_job"), (
            "the job record must be detached, or a second stop double-closes it"
        )
        assert _wait_dead(grandchild), "the job did not take the grandchild"
    finally:
        with contextlib.suppress(OSError):
            proc.kill()
        proc.wait()
