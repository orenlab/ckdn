# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""CLI alias expansion for ``ckdn run``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ckdn import cli
from ckdn.app import run as app_run
from ckdn.config import load_config
from ckdn.runner import RunOutcome


def _cfg(tmp: Path, body: str) -> Path:
    path = tmp / "ckdn.toml"
    path.write_text(
        f'[run]\nruns_dir = "{(tmp / "runs").as_posix()}"\n\n{body}',
        encoding="utf-8",
    )
    return path


def _two_member_alias(
    *,
    first: str,
    second: str,
    alias: str = "group",
    fail_fast: bool | None = None,
) -> str:
    """Build TOML for two atomic generic checks plus an alias."""
    lines = [
        f"[check.{first}]",
        'command = "true"',
        'parser = "generic"',
        f"[check.{second}]",
        'command = "true"',
        'parser = "generic"',
        f"[check.{alias}]",
        f'members = ["{first}", "{second}"]',
    ]
    if fail_fast is not None:
        lines.append(f"fail_fast = {str(fail_fast).lower()}")
    return "\n".join(lines) + "\n"


def _load_json_stream(text: str) -> list[Any]:
    decoder = json.JSONDecoder()
    docs: list[Any] = []
    idx = 0
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        obj, end = decoder.raw_decode(text, idx)
        docs.append(obj)
        idx = end
    return docs


def _outcome(run_dir: Path, rc: int) -> RunOutcome:
    return RunOutcome(
        run_dir=run_dir,
        tokens=["stub"],
        rc=rc,
        log_text="",
        started_at="2026-01-01T00:00:00+00:00",
        duration_s=0.01,
        timed_out=False,
        exec_note=None,
    )


def _check_from_run_dir(run_dir: Path) -> str:
    # ``{stamp}-{check}`` or ``{stamp}-{check}-{n}``
    rest = run_dir.name.split("-", 1)[1]
    if rest.rsplit("-", 1)[-1].isdigit():
        return rest.rsplit("-", 1)[0]
    return rest


@pytest.fixture
def fake_execute(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[int]]:
    """Map check name → queue of return codes (default 0)."""
    state: dict[str, list[int]] = {}

    def _execute(
        tokens: list[str],
        cwd: Path,
        run_dir: Path,
        timeout: float | None,
    ) -> RunOutcome:
        check = _check_from_run_dir(run_dir)
        queue = state.setdefault(check, [0])
        rc = queue.pop(0) if queue else 0
        return _outcome(run_dir, rc)

    monkeypatch.setattr(app_run, "execute", _execute)
    return state


def test_alias_runs_members_in_order(
    tmp_path: Path,
    fake_execute: dict[str, list[int]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_execute["pass_a"] = [0]
    fake_execute["pass_b"] = [0]
    cfg_path = _cfg(
        tmp_path,
        _two_member_alias(first="pass_a", second="pass_b"),
    )
    rc = cli.main(["run", "group", "--config", str(cfg_path)])
    assert rc == 0
    docs = _load_json_stream(capsys.readouterr().out)
    assert len(docs) == 1
    aggregate = docs[0]
    assert aggregate["schema"] == "ckdn.aggregate/1"
    assert aggregate["alias"] == "group" and aggregate["status"] == "pass"
    assert aggregate["rc"] == 0
    assert [m["check"] for m in aggregate["members"]] == ["pass_a", "pass_b"]
    assert all("run_dir" not in m for m in aggregate["members"])


def test_fail_fast_stops_after_first_failure(
    tmp_path: Path,
    fake_execute: dict[str, list[int]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_execute["fail_a"] = [1]
    fake_execute["pass_b"] = [0]
    cfg_path = _cfg(
        tmp_path,
        _two_member_alias(first="fail_a", second="pass_b"),
    )
    assert cli.main(["run", "group", "--config", str(cfg_path)]) == 1
    docs = _load_json_stream(capsys.readouterr().out)
    assert len(docs) == 1
    aggregate = docs[0]
    assert aggregate["alias"] == "group"
    assert aggregate["rc"] == 1
    assert [m["check"] for m in aggregate["members"]] == ["fail_a"]
    assert "run_dir" in aggregate["members"][0]


def test_fail_fast_false_runs_all(
    tmp_path: Path,
    fake_execute: dict[str, list[int]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_execute["fail_a"] = [1]
    fake_execute["pass_b"] = [0]
    cfg_path = _cfg(
        tmp_path,
        _two_member_alias(first="fail_a", second="pass_b", fail_fast=False),
    )
    assert cli.main(["run", "group", "--config", str(cfg_path)]) == 1
    docs = _load_json_stream(capsys.readouterr().out)
    assert len(docs) == 1
    aggregate = docs[0]
    assert [m["check"] for m in aggregate["members"]] == ["fail_a", "pass_b"]


def test_alias_rejects_extra_args(tmp_path: Path) -> None:
    cfg_path = _cfg(
        tmp_path,
        "[check.a]\n"
        'command = "true"\n'
        'parser = "generic"\n'
        "[check.group]\n"
        'members = ["a"]\n',
    )
    assert cli.main(["run", "group", "--config", str(cfg_path), "--", "-x"]) == 2


def test_checks_lists_alias(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg_path = _cfg(
        tmp_path,
        "[check.a]\n"
        'command = "true"\n'
        'parser = "generic"\n'
        "[check.group]\n"
        'members = ["a"]\n',
    )
    assert cli.main(["checks", "--config", str(cfg_path)]) == 0
    out = capsys.readouterr().out
    assert "group\talias=a\tfail_fast=True" in out
    assert "a\tparser=generic\ttrue" in out


def test_quiet_suppresses_alias_output(
    tmp_path: Path,
    fake_execute: dict[str, list[int]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_execute["a"] = [0]
    cfg_path = _cfg(
        tmp_path,
        "[check.a]\n"
        'command = "true"\n'
        'parser = "generic"\n'
        "[check.group]\n"
        'members = ["a"]\n',
    )
    assert cli.main(["run", "group", "--quiet", "--config", str(cfg_path)]) == 0
    assert capsys.readouterr().out == ""


def test_project_ckdn_toml_loads() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "ckdn.toml")
    assert cfg.checks["lint"].members == ("ruff",)
    assert cfg.checks["types"].members == ("ty", "mypy")
    assert "mypy" in cfg.checks
