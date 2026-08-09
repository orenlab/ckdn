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
from ckdn.app import run as app_run
from ckdn.app.errors import AppError
from ckdn.app.types import AtomicRunResult
from ckdn.config import CONFIG_NAME, STARTER_CONFIG, load_config
from ckdn.digest import DIGEST_NAME
from ckdn.parsers.base import ParseResult
from ckdn.runner import RunOutcome, create_run_dir, update_latest


def _cfg(tmp: Path, body: str) -> Path:
    path = tmp / CONFIG_NAME
    path.write_text(
        f'[run]\nruns_dir = "{(tmp / "runs").as_posix()}"\nkeep = 20\n\n{body}',
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
        env: dict[str, str] | None = None,
    ) -> RunOutcome:
        return _outcome(run_dir, 0)

    monkeypatch.setattr(app_run, "execute", _execute)


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


def test_main_run_pre_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    cfg = _cfg(
        tmp_path,
        (
            "[check.hooks]\n"
            'command = "pre-commit run --all-files"\n'
            'parser = "pre_commit"\n'
        ),
    )
    log = """\
Fail Hook................................................................Failed
- hook id: fail-hook
- exit code: 1

boom
"""

    def _execute(
        tokens: list[str],
        cwd: Path,
        run_dir: Path,
        timeout: float | None,
        env: dict[str, str] | None = None,
    ) -> RunOutcome:
        return RunOutcome(
            run_dir=run_dir,
            tokens=tokens,
            rc=1,
            log_text=log,
            started_at="2026-01-01T00:00:00+00:00",
            duration_s=0.01,
            timed_out=False,
            exec_note=None,
        )

    monkeypatch.setattr(app_run, "execute", _execute)
    rc = cli.main(["run", "--config", str(cfg), "hooks"])
    assert rc == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["status"] == "fail"
    assert doc["findings_total"] == 1
    assert doc["findings"][0]["id"] == "fail-hook"
    assert doc["summary"]["failed_hooks"] == ["fail-hook"]


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
        env: dict[str, str] | None = None,
    ) -> RunOutcome:
        return _outcome(run_dir, 0)

    monkeypatch.setattr(app_run, "get_parser", lambda _n: Boom())
    monkeypatch.setattr(app_run, "execute", _execute)
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
        env: dict[str, str] | None = None,
    ) -> RunOutcome:
        return _outcome(run_dir, 127, note="command not found: x")

    monkeypatch.setattr(app_run, "execute", _execute)
    rc = cli.main(["run", "--config", str(cfg), "ok", "--quiet"])
    assert rc == 127
    runs = tmp_path / "runs"
    # `.locks` also lives under runs_dir; a run dir is never dot-prefixed.
    latest = next(
        p
        for p in runs.iterdir()
        if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")
    )
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
        env: dict[str, str] | None = None,
    ) -> RunOutcome:
        seen["tokens"] = tokens
        return _outcome(run_dir, 0)

    monkeypatch.setattr(app_run, "execute", _execute)
    rc = cli.main(["run", "--config", str(cfg), "ok", "--", "--flag", "1"])
    assert rc == 0
    assert seen["tokens"][-2:] == ["--flag", "1"]


JUNIT_ALL_PASS = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="0" skipped="0" tests="1">
    <testcase classname="tests.ok" name="test_ok"/>
  </testsuite>
</testsuites>
"""


def test_main_run_cwd_separate_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    config_dir = tmp_path / "cfg"
    worktree = tmp_path / "wt"
    config_dir.mkdir()
    worktree.mkdir()
    cfg_path = config_dir / CONFIG_NAME
    cfg_path.write_text(
        '[run]\nruns_dir = ".agent-runs"\nkeep = 20\n\n'
        '[check.pt]\ncommand = "true"\nparser = "pytest"\n',
        encoding="utf-8",
    )

    def _execute(
        tokens: list[str],
        cwd: Path,
        run_dir: Path,
        timeout: float | None,
        env: dict[str, str] | None = None,
    ) -> RunOutcome:
        assert cwd == worktree.resolve()
        (run_dir / "junit.xml").write_text(JUNIT_ALL_PASS, encoding="utf-8")
        return _outcome(run_dir, 0)

    monkeypatch.setattr(app_run, "execute", _execute)
    rc = cli.main(["run", "--config", str(cfg_path), "--cwd", str(worktree), "pt"])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["status"] == "pass"
    assert (worktree / ".agent-runs").is_dir()


def test_main_broken_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raising(_args: argparse.Namespace) -> int:
        raise BrokenPipeError

    class _Parser:
        def parse_args(self, _raw: list[str]) -> argparse.Namespace:
            return argparse.Namespace(fn=_raising)

    monkeypatch.setattr(cli, "build_arg_parser", lambda: _Parser())
    monkeypatch.setattr(sys.stdout, "close", lambda: None)
    assert cli.main([]) == 0


def test_main_verify_and_lock_config(tmp_path: Path, capsys: Any) -> None:
    cfg_path = _cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n',
    )
    assert cli.main(["lock-config", "--config", str(cfg_path)]) == 0
    assert "wrote" in capsys.readouterr().out
    assert cli.main(["verify-config", "--config", str(cfg_path)]) == 0
    assert capsys.readouterr().out.strip() == "ok"
    assert cli.main(["verify-config", "--config", str(cfg_path), "--locked"]) == 0


def test_run_one_rejects_alias_as_atomic(tmp_path: Path) -> None:
    cfg_path = _cfg(
        tmp_path,
        '[check.a]\ncommand = "true"\nparser = "generic"\n[check.g]\nmembers = ["a"]\n',
    )
    cfg = load_config(cfg_path)
    alias = cfg.checks["g"]
    assert isinstance(cli.run_one(cfg, alias, extra=[], quiet=True), int)


def test_run_all_emits_aggregate(
    tmp_path: Path, stub_execute: None, capsys: Any
) -> None:
    cfg = _cfg(
        tmp_path,
        '[check.a]\ncommand = "true"\nparser = "generic"\n'
        '[check.b]\ncommand = "true"\nparser = "generic"\n'
        '[check.g]\nmembers = ["a"]\n',
    )
    assert cli.main(["run", "--all", "--config", str(cfg)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema"] == "ckdn.aggregate/1" and doc["alias"] == "*"
    assert [m["check"] for m in doc["members"]] == ["a", "b"]


def test_run_all_rejects_check_and_missing_target(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, '[check.a]\ncommand = "true"\nparser = "generic"\n')
    assert cli.main(["run", "--all", "a", "--config", str(cfg)]) == 2
    assert cli.main(["run", "--config", str(cfg)]) == 2


def test_checks_json(tmp_path: Path, capsys: Any) -> None:
    cfg = _cfg(
        tmp_path,
        '[check.a]\ncommand = "true"\nparser = "generic"\ntimeout = 5\n'
        '[check.g]\nmembers = ["a"]\n',
    )
    assert cli.main(["checks", "--json", "--config", str(cfg)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert set(doc) == {"checks"}
    by_name = {c["name"]: c for c in doc["checks"]}
    assert by_name["a"]["kind"] == "atomic" and by_name["a"]["timeout"] == 5.0
    assert by_name["g"]["kind"] == "alias" and by_name["g"]["members"] == ["a"]


def test_list_json(tmp_path: Path, stub_execute: None, capsys: Any) -> None:
    cfg = _cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    cli.main(["run", "--config", str(cfg), "ok", "--quiet"])
    capsys.readouterr()
    assert cli.main(["list", "--json", "--config", str(cfg)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert set(doc) == {"runs"}
    last = doc["runs"][-1]
    assert last["check"] == "ok" and last["status"] == "pass"


def test_app_error_is_a_message_and_exit_2_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refused start is not a red check.

    `run --all` let AppError escape as a traceback and exit 1 — the code that
    means "this check failed" — so CI could not tell a lock conflict from a
    genuine failure.
    """
    cfg = _cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')

    def _refuse(*_a: object, **_k: object) -> None:
        raise AppError("check 'ok' is already running in this workspace")

    monkeypatch.setattr("ckdn.cli.run_all", _refuse)
    rc = cli.main(["run", "--all", "--config", str(cfg)])

    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("ckdn: ")
    assert "already running" in err


def test_baseline_refuses_to_record_an_interrupted_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C during `ckdn baseline` used to be silently accepted.

    The partial findings overwrote the accepted set and the command exited 0,
    so the next gate announced the entire old backlog as new.
    """
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        '{"version": 1, "checks": {"ok": ["deadbeef"]}}', encoding="utf-8"
    )
    cfg = tmp_path / "ckdn.toml"
    cfg.write_text(
        '[run]\nruns_dir = "runs"\nbaseline = "baseline.json"\n\n'
        '[check.ok]\ncommand = "true"\nparser = "generic"\n',
        encoding="utf-8",
    )
    before = baseline_path.read_text(encoding="utf-8")

    def _interrupted(*_a: object, **_k: object) -> AtomicRunResult:
        return AtomicRunResult(
            check="ok",
            status="error",
            rc=130,
            run_dir=tmp_path / "runs" / "x",
            digest={"check": "ok", "interrupted": True, "findings": []},
            exit_code=130,
        )

    monkeypatch.setattr("ckdn.cli.app_run_one", _interrupted)
    rc = cli.main(["baseline", "ok", "--config", str(cfg)])

    assert rc == 2
    assert "interrupted" in capsys.readouterr().err
    assert baseline_path.read_text(encoding="utf-8") == before, (
        "a partial run must not overwrite the accepted findings"
    )


# --- argument and configuration refusals -------------------------------------


def test_run_all_and_a_check_name_are_mutually_exclusive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    rc = cli.main(["run", "--all", "ok", "--config", str(cfg)])
    assert rc == 2
    assert "not both" in capsys.readouterr().err


def test_run_all_refuses_extra_arguments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    rc = cli.main(["run", "--all", "--config", str(cfg), "--", "-k", "smoke"])
    assert rc == 2
    assert "does not accept extra arguments" in capsys.readouterr().err


def test_run_quiet_prints_nothing_on_a_pass(
    tmp_path: Path, stub_execute: None, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    assert cli.main(["run", "--config", str(cfg), "--quiet", "ok"]) == 0
    assert capsys.readouterr().out == ""


def test_run_one_wrapper_reports_an_app_error_as_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = load_config(
        _cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n'),
        cwd=tmp_path,
    )

    def _refuse(*_a: object, **_k: object) -> None:
        raise AppError("no")

    monkeypatch.setattr("ckdn.cli.app_run_one", _refuse)
    assert cli.run_one(cfg, cfg.checks["ok"], extra=[], quiet=True) == 2
    assert "ckdn: no" in capsys.readouterr().err


def test_verify_config_prints_every_error_and_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = tmp_path / CONFIG_NAME
    cfg.write_text(
        '[run]\ncommand_policy = "workspace"\n\n'
        '[check.bad]\ncommand = "cat /etc/passwd"\nparser = "generic"\n',
        encoding="utf-8",
    )
    rc = cli.main(["verify-config", "--config", str(cfg), "--locked"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "escapes workspace" in err
    assert "lock file not found" in err


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        # No `baseline = "…"` in [run]: nothing to record into.
        (["baseline", "ok"], "under [run]"),
        # A run reference that resolves to nothing -- and the message says
        # which of the two reasons it was, rather than "nothing has run yet".
        (["annotate", "no-such-run"], "no run matching 'no-such-run' (unknown,"),
    ],
)
def test_commands_that_need_more_than_a_config_say_so(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    expected: str,
) -> None:
    cfg = _cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    rc = cli.main([*argv, "--config", str(cfg)])
    assert rc == 2
    assert expected in capsys.readouterr().err


def test_baseline_rejects_an_unknown_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = tmp_path / CONFIG_NAME
    cfg.write_text(
        '[run]\nruns_dir = "runs"\nbaseline = "baseline.json"\n\n'
        '[check.ok]\ncommand = "true"\nparser = "generic"\n',
        encoding="utf-8",
    )
    rc = cli.main(["baseline", "nope", "--config", str(cfg), "--cwd", str(tmp_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown check 'nope'" in err
    assert "configured: ok" in err


def test_baseline_refuses_to_record_an_untrusted_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A parse_mismatch is no basis for accepting findings.

    Recording the empty set it produced would mark every existing finding
    "new" on the next run.
    """
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        '{"version": 1, "checks": {"ok": ["deadbeef"]}}', encoding="utf-8"
    )
    cfg = tmp_path / CONFIG_NAME
    cfg.write_text(
        '[run]\nruns_dir = "runs"\nbaseline = "baseline.json"\n\n'
        '[check.ok]\ncommand = "true"\nparser = "generic"\n',
        encoding="utf-8",
    )
    before = baseline_path.read_text(encoding="utf-8")

    def _mismatch(*_a: object, **_k: object) -> AtomicRunResult:
        return AtomicRunResult(
            check="ok",
            status="parse_mismatch",
            rc=1,
            run_dir=tmp_path / "runs" / "x",
            digest={"check": "ok", "findings": []},
            exit_code=1,
        )

    monkeypatch.setattr("ckdn.cli.app_run_one", _mismatch)
    rc = cli.main(["baseline", "ok", "--config", str(cfg), "--cwd", str(tmp_path)])
    assert rc == 2
    assert "not trustworthy enough" in capsys.readouterr().err
    assert baseline_path.read_text(encoding="utf-8") == before


def test_baseline_records_every_member_of_an_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = tmp_path / CONFIG_NAME
    cfg.write_text(
        '[run]\nruns_dir = "runs"\nbaseline = "baseline.json"\n\n'
        '[check.a]\ncommand = "true"\nparser = "generic"\n\n'
        '[check.b]\ncommand = "true"\nparser = "generic"\n\n'
        '[check.both]\nmembers = ["a", "b"]\n',
        encoding="utf-8",
    )

    def _one(_cfg: Any, check: Any, extra: Any) -> AtomicRunResult:
        # The recorded set must not be the digest's top-N slice.
        assert check.options["top"] == cli.BASELINE_TOP
        return AtomicRunResult(
            check=check.name,
            status="fail",
            rc=1,
            run_dir=tmp_path / "runs" / check.name,
            digest={
                "check": check.name,
                "findings": [{"id": f"{check.name}-1", "message": "x"}],
            },
            exit_code=1,
        )

    monkeypatch.setattr("ckdn.cli.app_run_one", _one)
    rc = cli.main(["baseline", "both", "--config", str(cfg), "--cwd", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "recorded 1 finding(s) for a" in out
    assert "recorded 1 finding(s) for b" in out
    recorded = json.loads((tmp_path / "baseline.json").read_text(encoding="utf-8"))
    assert sorted(recorded["checks"]) == ["a", "b"]


def test_a_config_error_is_a_message_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = tmp_path / CONFIG_NAME
    cfg.write_text('[check.a]\nparser = "generic"\n', encoding="utf-8")
    rc = cli.main(["checks", "--config", str(cfg)])
    assert rc == 2
    assert "ckdn: [check.a]" in capsys.readouterr().err


def test_an_interrupt_outside_a_check_still_exits_130(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')

    def _interrupt(*_a: object, **_k: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("ckdn.cli.list_runs", _interrupt)
    rc = cli.main(["list", "--config", str(cfg)])
    assert rc == 130
    assert capsys.readouterr().err == "ckdn: interrupted\n"


def test_run_one_wrapper_prints_the_digest_when_not_quiet(
    tmp_path: Path, stub_execute: None, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = load_config(
        _cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n'),
        cwd=tmp_path,
    )
    result = cli.run_one(cfg, cfg.checks["ok"], extra=[], quiet=False)
    assert not isinstance(result, int)
    assert json.loads(capsys.readouterr().out)["check"] == "ok"


def test_gate_flag_makes_the_exit_follow_the_baseline_not_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--gate` is the CI switch: only *new* findings should turn a job red."""
    cfg = tmp_path / CONFIG_NAME
    cfg.write_text(
        f'[run]\nruns_dir = "{(tmp_path / "runs").as_posix()}"\n'
        'baseline = "baseline.json"\n\n'
        '[check.ok]\ncommand = "true"\nparser = "generic"\n',
        encoding="utf-8",
    )

    def _failing(*_a: object, **_k: object) -> AtomicRunResult:
        return AtomicRunResult(
            check="ok",
            status="fail",
            rc=1,
            run_dir=tmp_path / "runs" / "x",
            digest={"check": "ok", "gate": {"status": "pass"}, "findings": []},
            exit_code=1,
        )

    monkeypatch.setattr("ckdn.cli.run_check", _failing)
    args = ["run", "ok", "--config", str(cfg), "--cwd", str(tmp_path), "--quiet"]
    # Every finding is already accepted: the gate passes, the run still failed.
    assert cli.main([*args, "--gate"]) == 0
    # Without --gate the exit reports execution truth, unchanged.
    assert cli.main(args) == 1
