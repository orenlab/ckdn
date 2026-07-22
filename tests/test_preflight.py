# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Pre-flight diagnostics (`ckdn doctor`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ckdn import cli
from ckdn.config import load_config
from ckdn.preflight import diagnose


def _cfg(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "ckdn.toml"
    path.write_text(f'[run]\nruns_dir = ".agent-runs"\n\n{body}', encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _all_execs_present(monkeypatch: pytest.MonkeyPatch) -> None:
    # Default: every executable resolves, so tests assert on parser/flag logic.
    monkeypatch.setattr("ckdn.preflight.shutil.which", lambda exe: f"/usr/bin/{exe}")


def _diag(tmp_path: Path, body: str) -> list[tuple[str, str, str]]:
    cfg = load_config(_cfg(tmp_path, body), cwd=tmp_path)
    return [(d.check, d.level, d.message) for d in diagnose(cfg)]


def test_clean_config_has_no_diagnostics(tmp_path: Path) -> None:
    diags = _diag(
        tmp_path,
        '[check.pytest]\ncommand = "pytest --junitxml {run_dir}/junit.xml"\n'
        'parser = "pytest"\n',
    )
    assert diags == []


def test_missing_executable_is_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ckdn.preflight.shutil.which", lambda exe: None)
    diags = _diag(
        tmp_path,
        '[check.t]\ncommand = "nope --junitxml {run_dir}/junit.xml"\n'
        'parser = "pytest"\n',
    )
    assert ("t", "error", "executable not found on PATH: nope") in diags


def test_file_parser_missing_report_is_warning(tmp_path: Path) -> None:
    diags = _diag(
        tmp_path, '[check.pytest]\ncommand = "pytest -q"\nparser = "pytest"\n'
    )
    assert len(diags) == 1
    check, level, msg = diags[0]
    assert (check, level) == ("pytest", "warning")
    assert "junit.xml" in msg


def test_report_option_override_is_honored(tmp_path: Path) -> None:
    # custom report name present in the command -> no warning
    assert (
        _diag(
            tmp_path,
            '[check.ruff]\ncommand = "ruff check --output-file {run_dir}/lint.json ."\n'
            'parser = "ruff"\nreport = "lint.json"\n',
        )
        == []
    )


def test_mypy_json_mode_needs_output_flag(tmp_path: Path) -> None:
    diags = _diag(
        tmp_path,
        '[check.mypy]\ncommand = "mypy src"\nparser = "mypy"\nformat = "json"\n',
    )
    assert diags and diags[0][1] == "warning" and "--output json" in diags[0][2]
    # with the flag present, no warning
    assert (
        _diag(
            tmp_path,
            '[check.mypy]\ncommand = "mypy src --output json"\nparser = "mypy"\n'
            'format = "json"\n',
        )
        == []
    )


def test_pyright_and_reformat_flag_hints(tmp_path: Path) -> None:
    diags = _diag(
        tmp_path,
        '[check.pyright]\ncommand = "pyright"\nparser = "pyright"\n'
        '[check.fmt]\ncommand = "ruff format ."\nparser = "reformat"\n',
    )
    by_check = {c: m for c, _, m in diags}
    assert "--outputjson" in by_check["pyright"]
    assert "--check" in by_check["fmt"]


def test_aliases_are_skipped(tmp_path: Path) -> None:
    diags = _diag(
        tmp_path,
        '[check.ruff]\ncommand = "ruff check --output-file {run_dir}/ruff.json ."\n'
        'parser = "ruff"\n[check.lint]\nmembers = ["ruff"]\n',
    )
    assert all(c != "lint" for c, _, _ in diags)


# --- CLI ------------------------------------------------------------------


def test_doctor_ok_exit_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(
        tmp_path,
        '[check.pytest]\ncommand = "pytest --junitxml {run_dir}/junit.xml"\n'
        'parser = "pytest"\n',
    )
    assert cli.main(["doctor", "--config", str(cfg)]) == 0
    assert "ok:" in capsys.readouterr().out


def test_doctor_error_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("ckdn.preflight.shutil.which", lambda exe: None)
    cfg = _cfg(tmp_path, '[check.t]\ncommand = "nope"\nparser = "generic"\n')
    assert cli.main(["doctor", "--config", str(cfg)]) == 1
    assert "executable not found" in capsys.readouterr().err


def test_doctor_warning_is_advisory_unless_strict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(tmp_path, '[check.pytest]\ncommand = "pytest -q"\nparser = "pytest"\n')
    assert cli.main(["doctor", "--config", str(cfg)]) == 0  # warning only
    capsys.readouterr()
    assert cli.main(["doctor", "--config", str(cfg), "--strict"]) == 1
