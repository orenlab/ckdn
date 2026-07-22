# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Unit tests for process execution and run-directory lifecycle."""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ckdn.runner import (
    LATEST_FILE,
    LATEST_LINK,
    LOG_NAME,
    RC_NOT_FOUND,
    RC_TIMEOUT,
    build_tokens,
    create_run_dir,
    execute,
    list_run_dirs,
    prune,
    resolve_run_dir,
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

    monkeypatch.setattr(subprocess, "run", _boom)
    outcome = execute(["true"], tmp_path, run_dir, None)
    assert outcome.rc == RC_NOT_FOUND
    assert outcome.exec_note is not None
    assert "failed to start" in outcome.exec_note


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
