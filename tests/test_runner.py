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
    RunLockError,
    _pid_alive,
    build_tokens,
    create_run_dir,
    execute,
    list_run_dirs,
    prune,
    resolve_run_dir,
    run_lock,
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


def _wait_dead(pid: int, limit: float = 10.0) -> bool:
    """Liveness the same way the runner sees it (portable across OSes).

    Both directions of `_pid_alive` are pinned independently by the run-lock
    tests below, so reusing it here is not circular.
    """
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
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
    timer = threading.Timer(1.5, _thread.interrupt_main)
    timer.start()
    try:
        outcome = execute(
            [sys.executable, "-c", _SPAWNS_GRANDCHILD], tmp_path, run_dir, None
        )
    finally:
        timer.cancel()

    assert outcome.interrupted is True and outcome.timed_out is False
    assert outcome.rc == RC_INTERRUPTED == 130
    assert "interrupted by SIGINT" in (outcome.exec_note or "")
    assert (run_dir / LOG_NAME).exists(), "evidence must exist after an interrupt"
    grandchild = int(outcome.log_text.strip())
    assert _wait_dead(grandchild), "grandchild outlived the interrupt"


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
    """Windows cannot use `os.kill(pid, 0)`; both branches must agree here."""
    live = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    dead = subprocess.Popen([sys.executable, "-c", ""])
    dead.wait()
    try:
        assert _pid_alive(live.pid) is True
        assert _pid_alive(dead.pid) is False
        assert _pid_alive(0) is False
        assert _pid_alive(-1) is False
        # `os.kill` raises OverflowError here, which is not an OSError and so
        # escaped every handler: the check stayed wedged until someone deleted
        # the lock file by hand.
        assert _pid_alive(2**31 + 5) is False
    finally:
        live.kill()
        live.wait()


_HOLD_LOCK = """
import sys, time
sys.path.insert(0, sys.argv[1])
from ckdn.runner import run_lock
with run_lock(__import__("pathlib").Path(sys.argv[2]), sys.argv[3]):
    print("held", flush=True)
    time.sleep(60)
"""


def test_run_lock_refuses_a_second_live_run(tmp_path: Path) -> None:
    """Another *process* holds it, which is what the lock is actually for."""
    src = str(Path(__file__).resolve().parent.parent / "src")
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK, src, str(tmp_path), "pytest"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
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
    lock = tmp_path / ".locks" / "z.lock"
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


def test_the_lock_records_the_holder_and_empties_it_on_exit(
    tmp_path: Path,
) -> None:
    lock = tmp_path / ".locks" / "x.lock"
    with run_lock(tmp_path, "x"):
        assert lock.read_text(encoding="utf-8").strip() == f"ckdn pid {os.getpid()}"
    assert lock.read_text(encoding="utf-8") == "", (
        "an empty record is how the next run knows this one finished"
    )


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
