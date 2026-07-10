# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Unit tests for process execution and run-directory lifecycle."""

from __future__ import annotations

import datetime as dt
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


def test_list_and_prune(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    assert list_run_dirs(runs) == []
    dirs = [create_run_dir(runs, f"c{i}") for i in range(5)]
    assert len(list_run_dirs(runs)) == 5
    assert prune(runs, 0) == 0
    removed = prune(runs, 2)
    assert removed == 3
    assert list_run_dirs(runs) == dirs[-2:]
