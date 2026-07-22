# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Unit tests for process execution and run-directory lifecycle."""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ckdn.runner import (
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    real_wait = subprocess.Popen.wait
    seen: list[int] = []

    def _fake_wait(self: subprocess.Popen[bytes], timeout: float | None = None) -> int:
        seen.append(1)
        if len(seen) == 1:  # first wait: the user pressed Ctrl-C
            time.sleep(1.0)  # let the child emit its grandchild's pid first
            raise KeyboardInterrupt
        return int(real_wait(self, timeout=timeout))

    monkeypatch.setattr(subprocess.Popen, "wait", _fake_wait)
    outcome = execute(
        [sys.executable, "-c", _SPAWNS_GRANDCHILD], tmp_path, run_dir, None
    )

    assert outcome.interrupted is True and outcome.timed_out is False
    assert outcome.rc == RC_INTERRUPTED == 130
    assert "interrupted by SIGINT" in (outcome.exec_note or "")
    assert (run_dir / LOG_NAME).exists(), "evidence must exist after an interrupt"
    grandchild = int(outcome.log_text.strip())
    assert _wait_dead(grandchild), "grandchild outlived the interrupt"


def test_pid_alive_tells_a_live_process_from_a_dead_one() -> None:
    """Windows cannot use `os.kill(pid, 0)`; both branches must agree here.

    Reading a dead pid as alive would make a stale lock permanent — the check
    could never be run again in that workspace.
    """
    live = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    dead = subprocess.Popen([sys.executable, "-c", ""])
    dead.wait()
    try:
        assert _pid_alive(live.pid) is True
        assert _pid_alive(dead.pid) is False
        assert _pid_alive(0) is False
        assert _pid_alive(-1) is False
    finally:
        live.kill()
        live.wait()


def test_run_lock_refuses_a_second_live_run(tmp_path: Path) -> None:
    holder = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    try:
        lock = tmp_path / ".locks" / "pytest.lock"
        lock.parent.mkdir(parents=True)
        lock.write_text(str(holder.pid), encoding="utf-8")
        with (
            pytest.raises(RunLockError, match="already running"),
            run_lock(tmp_path, "pytest"),
        ):
            pass
    finally:
        holder.kill()
        holder.wait()


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


def test_a_zero_byte_lock_is_reclaimed(tmp_path: Path) -> None:
    """A crash between creating the lock file and writing the pid into it.

    The holder is unreadable, so it must be treated as stale — otherwise the
    check is wedged forever with no process to blame.
    """
    lock = tmp_path / ".locks" / "y.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("", encoding="utf-8")
    with run_lock(tmp_path, "y"):
        assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())
    assert not lock.exists()


def test_run_lock_reclaims_a_stale_lock_and_releases(tmp_path: Path) -> None:
    dead = subprocess.Popen([sys.executable, "-c", ""])
    dead.wait()
    lock = tmp_path / ".locks" / "x.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(str(dead.pid), encoding="utf-8")
    with run_lock(tmp_path, "x"):
        assert lock.exists()
    assert not lock.exists(), "the lock must be released on exit"


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


def test_list_and_prune(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    assert list_run_dirs(runs) == []
    dirs = [create_run_dir(runs, f"c{i}") for i in range(5)]
    assert len(list_run_dirs(runs)) == 5
    assert prune(runs, 0) == 0
    removed = prune(runs, 2)
    assert removed == 3
    assert list_run_dirs(runs) == dirs[-2:]
