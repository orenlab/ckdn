# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""CLI command coverage beyond alias expansion."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from ckdn import cli
from ckdn.config import CONFIG_NAME, STARTER_CONFIG, load_config
from ckdn.digest import DIGEST_NAME
from ckdn.parsers.base import ParseResult
from ckdn.runner import RunOutcome, create_run_dir, update_latest


def _cfg(tmp: Path, body: str) -> Path:
    path = tmp / CONFIG_NAME
    path.write_text(
        f'[run]\nruns_dir = "{tmp / "runs"}"\nkeep = 20\n\n{body}',
        encoding="utf-8",
    )
    return path


def _outcome(run_dir: Path, rc: int = 0, note: str | None = None) -> RunOutcome:
    return RunOutcome(
        run_dir=run_dir,
        tokens=["stub"],
        rc=rc,
        log_text="ok\n",
        started_at="2026-01-01T00:00:00+00:00",
        duration_s=0.01,
        timed_out=False,
        exec_note=note,
    )


@pytest.fixture
def stub_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    def _execute(
        tokens: list[str],
        cwd: Path,
        run_dir: Path,
        timeout: float | None,
    ) -> RunOutcome:
        return _outcome(run_dir, 0)

    monkeypatch.setattr(cli, "execute", _execute)


def test_main_run_generic(tmp_path: Path, stub_execute: None, capsys: Any) -> None:
    cfg = _cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n',
    )
    rc = cli.main(["run", "--config", str(cfg), "ok"])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["status"] == "pass"
    assert doc["check"] == "ok"


def test_main_unknown_check(tmp_path: Path) -> None:
    cfg = _cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n',
    )
    assert cli.main(["run", "--config", str(cfg), "nope"]) == 2


def test_main_unknown_parser(tmp_path: Path) -> None:
    cfg = _cfg(
        tmp_path,
        '[check.bad]\ncommand = "true"\nparser = "no_such_parser"\n',
    )
    assert cli.main(["run", "--config", str(cfg), "bad"]) == 2


def test_parser_crash_becomes_parse_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    cfg = _cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n',
    )

    class Boom:
        name = "generic"

        def parse(self, *_a: object, **_k: object) -> ParseResult:
            raise RuntimeError("boom")

    def _execute(
        tokens: list[str],
        cwd: Path,
        run_dir: Path,
        timeout: float | None,
    ) -> RunOutcome:
        return _outcome(run_dir, 0)

    monkeypatch.setattr(cli, "get_parser", lambda _n: Boom())
    monkeypatch.setattr(cli, "execute", _execute)
    rc = cli.main(["run", "--config", str(cfg), "ok"])
    assert rc == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["status"] == "parse_mismatch"
    assert any("crashed" in n for n in doc.get("notes", []))


def test_exec_note_prepended(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n',
    )

    def _execute(
        tokens: list[str],
        cwd: Path,
        run_dir: Path,
        timeout: float | None,
    ) -> RunOutcome:
        return _outcome(run_dir, 127, note="command not found: x")

    monkeypatch.setattr(cli, "execute", _execute)
    rc = cli.main(["run", "--config", str(cfg), "ok", "--quiet"])
    assert rc == 127
    runs = tmp_path / "runs"
    latest = next(p for p in runs.iterdir() if p.is_dir() and not p.is_symlink())
    doc = json.loads((latest / DIGEST_NAME).read_text(encoding="utf-8"))
    assert doc["notes"][0].startswith("command not found")


def test_show_list_gc(tmp_path: Path, stub_execute: None) -> None:
    cfg = _cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n',
    )
    assert cli.main(["run", "--config", str(cfg), "ok", "--quiet"]) == 0
    assert cli.main(["show", "--config", str(cfg)]) == 0
    assert cli.main(["list", "--config", str(cfg), "-n", "5"]) == 0
    assert cli.main(["checks", "--config", str(cfg)]) == 0
    assert cli.main(["gc", "--config", str(cfg), "--keep", "1"]) == 0


def test_show_errors(tmp_path: Path) -> None:
    cfg = _cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n',
    )
    assert cli.main(["show", "--config", str(cfg)]) == 2
    runs = tmp_path / "runs"
    runs.mkdir()
    empty = create_run_dir(runs, "empty")
    update_latest(runs, empty)
    assert cli.main(["show", "--config", str(cfg)]) == 2
    (empty / DIGEST_NAME).write_text("{not-json", encoding="utf-8")
    assert cli.main(["show", "--config", str(cfg)]) == 2
    (empty / DIGEST_NAME).write_text("[1,2]", encoding="utf-8")
    assert cli.main(["show", "--config", str(cfg)]) == 2


def test_list_corrupt_digest(tmp_path: Path, capsys: Any) -> None:
    cfg = _cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n',
    )
    runs = tmp_path / "runs"
    run_dir = create_run_dir(runs, "x")
    (run_dir / DIGEST_NAME).write_text("{bad", encoding="utf-8")
    assert cli.main(["list", "--config", str(cfg)]) == 0
    assert "corrupt" in capsys.readouterr().out


def test_init_writes_and_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init"]) == 0
    written = (tmp_path / CONFIG_NAME).read_text(encoding="utf-8")
    assert written == STARTER_CONFIG
    assert cli.main(["init"]) == 2


def test_main_config_error(tmp_path: Path) -> None:
    missing = tmp_path / "nope.toml"
    assert cli.main(["checks", "--config", str(missing)]) == 2


def test_main_extra_after_dashdash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n',
    )
    seen: dict[str, list[str]] = {}

    def _execute(
        tokens: list[str],
        cwd: Path,
        run_dir: Path,
        timeout: float | None,
    ) -> RunOutcome:
        seen["tokens"] = tokens
        return _outcome(run_dir, 0)

    monkeypatch.setattr(cli, "execute", _execute)
    rc = cli.main(["run", "--config", str(cfg), "ok", "--", "--flag", "1"])
    assert rc == 0
    assert seen["tokens"][-2:] == ["--flag", "1"]


def test_main_broken_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raising(_args: argparse.Namespace) -> int:
        raise BrokenPipeError

    class _Parser:
        def parse_args(self, _raw: list[str]) -> argparse.Namespace:
            return argparse.Namespace(fn=_raising)

    monkeypatch.setattr(cli, "build_arg_parser", lambda: _Parser())
    monkeypatch.setattr(sys.stdout, "close", lambda: None)
    assert cli.main([]) == 0


def test_run_one_rejects_alias_as_atomic(tmp_path: Path) -> None:
    cfg_path = _cfg(
        tmp_path,
        '[check.a]\ncommand = "true"\nparser = "generic"\n[check.g]\nmembers = ["a"]\n',
    )
    cfg = load_config(cfg_path)
    alias = cfg.checks["g"]
    assert isinstance(cli.run_one(cfg, alias, extra=[], quiet=True), int)
