# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Finding baselines: three axes (execution / findings / gate).

The invariant under test: baseline never changes execution truth. It only
classifies findings and derives a separate gate that may accept a nonzero exit
for CI — but only when the evidence is trustworthy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ckdn import DIGEST_SCHEMA, cli
from ckdn.app import run as app_run
from ckdn.app import run_alias, run_one
from ckdn.baseline import (
    combine_gate,
    fingerprint,
    gate,
    gate_exit,
    load,
    save,
)
from ckdn.config import Config, load_config
from ckdn.digest import META_NAME
from ckdn.parsers.base import Finding, ParseResult
from ckdn.runner import LOG_NAME, RunOutcome
from ckdn.schema import load_schema

# --- pure functions -------------------------------------------------------


def test_fingerprint_ignores_line_and_column_drift() -> None:
    top = {"kind": "lint", "message": "unused", "location": "a.py:5:2"}
    moved = {"kind": "lint", "message": "unused", "location": "a.py:99:7"}
    assert fingerprint("ruff", top) == fingerprint("ruff", moved)
    assert fingerprint("ruff", top) != fingerprint("ruff", {**top, "message": "x"})
    assert fingerprint("ruff", top) != fingerprint("ruff", {**top, "kind": "y"})
    assert fingerprint("ruff", top) != fingerprint("mypy", top)


def test_load_save_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    save(path, {"ruff": {"x", "y"}, "pytest": {"z"}})
    assert load(path) == {"ruff": {"x", "y"}, "pytest": {"z"}}
    assert load(tmp_path / "missing.json") == {}


def test_gate_rules() -> None:
    # a nonzero exit whose findings are ALL baselined is what the gate exists
    # for: known findings, none new -> pass.
    assert gate("fail", True, 0, known_count=2, gate_failures=()) == {
        "status": "pass",
        "policy": "no_new_findings",
    }
    # a green run has nothing to classify and still gates pass
    assert gate("pass", True, 0, known_count=0, gate_failures=()) == {
        "status": "pass",
        "policy": "no_new_findings",
    }
    assert gate("fail", True, 3, known_count=0, gate_failures=()) == {
        "status": "fail",
        "policy": "no_new_findings",
        "reason": "3 new finding(s) not in baseline",
    }
    # untrustworthy evidence is never accepted by baseline
    for status in ("error", "parse_mismatch"):
        assert gate(status, True, 0, known_count=1, gate_failures=()) == {
            "status": "unavailable",
            "policy": "no_new_findings",
            "reason": (f"execution '{status}' — evidence not trustworthy for baseline"),
        }
    assert gate("fail", False, 0, known_count=1, gate_failures=()) == {
        "status": "unavailable",
        "policy": "no_new_findings",
        "reason": "execution 'fail' — evidence not trustworthy for baseline",
    }


def test_gate_never_passes_a_failure_it_cannot_account_for() -> None:
    """A `fail` with zero classified findings is not "no new findings".

    Nothing was classified, so the baseline has no evidence that the failure
    is the accepted one — it must not hand CI a green exit.
    """
    unaccounted = {
        "status": "unavailable",
        "policy": "no_new_findings",
        "reason": (
            "execution 'fail' produced no findings for the baseline to classify"
        ),
    }
    decision = gate("fail", True, 0, known_count=0, gate_failures=())
    assert decision == unaccounted
    # and the process exit stays the honest execution exit, not 0
    assert gate_exit(decision["status"], 1) == 1


def test_gate_never_waives_a_policy_gate_failure() -> None:
    """`gate_failures` carry no fingerprint, so a baseline can never accept one."""
    assert gate(
        "fail",
        True,
        0,
        known_count=3,
        gate_failures=["line coverage 80.0% is below fail_under=95.0%"],
    ) == {
        "status": "fail",
        "policy": "no_new_findings",
        "reason": (
            "policy gate not satisfied: line coverage 80.0% is below fail_under=95.0%"
        ),
    }
    # several policy gates are reported in order, and new findings do not
    # displace them
    assert gate(
        "fail",
        True,
        2,
        known_count=0,
        gate_failures=["coverage below fail_under", "score below minimum"],
    ) == {
        "status": "fail",
        "policy": "no_new_findings",
        "reason": (
            "policy gate not satisfied: coverage below fail_under; score below minimum"
        ),
    }


def test_gate_exit() -> None:
    assert gate_exit("pass", 1) == 0
    assert gate_exit("fail", 1) == 1
    # a gate fail is exit 1 even when execution exited something else, so the
    # `fail` branch is not just the execution exit passing through
    assert gate_exit("fail", 4) == 1
    assert gate_exit("unavailable", 4) == 4  # honest execution exit
    assert gate_exit(None, 7) == 7


def test_combine_gate() -> None:
    passed = {"status": "pass", "policy": "no_new_findings"}
    failed = {"status": "fail", "policy": "no_new_findings"}
    unavailable = {"status": "unavailable", "policy": "no_new_findings"}
    assert combine_gate([]) is None
    # the whole document is pinned, `policy` included: consumers read that key
    assert combine_gate([{"gate": passed}, {"gate": passed}]) == passed
    assert combine_gate([{"gate": passed}, {"gate": failed}]) == failed
    assert combine_gate([{"gate": failed}, {"gate": unavailable}]) == unavailable


# --- integration: execution truth is preserved ----------------------------


def _fps(findings: list[Finding]) -> set[str]:
    """The accepted-fingerprint set a baseline file would hold for check ``x``."""
    return {fingerprint("x", finding.to_dict()) for finding in findings}


def _cfg_with_baseline(tmp_path: Path) -> Config:
    (tmp_path / "ckdn.toml").write_text(
        '[run]\nruns_dir = ".agent-runs"\nbaseline = "b.json"\n'
        '[check.x]\ncommand = "cmd"\nparser = "fp"\n',
        encoding="utf-8",
    )
    return load_config(tmp_path / "ckdn.toml", cwd=tmp_path)


def _finding_parser(finding: Finding, *, parser_ok: bool = True) -> object:
    class _FP:
        name = "fp"

        def parse(self, ctx: object) -> ParseResult:
            return ParseResult(parser_ok=parser_ok, findings=[finding])

    return _FP()


def _stub_execute(monkeypatch: pytest.MonkeyPatch, rc: int) -> None:
    def _exec(
        tokens: list[str],
        cwd: Path,
        run_dir: Path,
        timeout: float | None,
        env: dict[str, str] | None = None,
    ) -> RunOutcome:
        (run_dir / LOG_NAME).write_text("", encoding="utf-8")
        return RunOutcome(
            run_dir=run_dir,
            tokens=tokens,
            rc=rc,
            log_text="",
            started_at="2026-01-01T00:00:00+00:00",
            duration_s=0.0,
            timed_out=False,
            exec_note=None,
        )

    monkeypatch.setattr(app_run, "execute", _exec)


def test_baseline_preserves_execution_truth_and_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg_with_baseline(tmp_path)
    finding = Finding(id="F", kind="k", message="m", location="a.py:5")
    monkeypatch.setattr(app_run, "get_parser", lambda _n: _finding_parser(finding))
    _stub_execute(monkeypatch, rc=1)

    # no baseline yet: finding is new -> execution fail, gate fail
    first = run_one(cfg, cfg.checks["x"], extra=[]).digest
    assert first["status"] == "fail"
    assert first["gate"]["status"] == "fail"
    assert first["baseline"] == {"known": 0, "new": 1}

    # accept it into the baseline
    assert cfg.baseline_path is not None
    save(cfg.baseline_path, {"x": _fps([finding])})

    # same finding is now known: execution truth UNCHANGED, gate passes
    second = run_one(cfg, cfg.checks["x"], extra=[]).digest
    assert second["status"] == "fail" and second["rc"] == 1  # never upgraded
    assert second["gate"]["status"] == "pass"
    assert second["baseline"] == {"known": 1, "new": 0}
    assert second["findings"][0]["baselined"] is True
    # the digest with baseline/gate still conforms to the published schema
    Draft202012Validator(load_schema(DIGEST_SCHEMA)).validate(second)


def test_baseline_counts_every_finding_and_survives_an_empty_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`known` / `new` are counts, not flags — and zero findings is not a crash."""
    cfg = _cfg_with_baseline(tmp_path)
    known = [Finding(id=f"K{i}", kind="k", message=f"k{i}") for i in range(2)]
    unknown = [Finding(id=f"N{i}", kind="k", message=f"n{i}") for i in range(2)]

    class _FP:
        name = "fp"

        def parse(self, ctx: object) -> ParseResult:
            return ParseResult(parser_ok=True, findings=[*known, *unknown])

    monkeypatch.setattr(app_run, "get_parser", lambda _n: _FP())
    _stub_execute(monkeypatch, rc=1)
    assert cfg.baseline_path is not None
    save(cfg.baseline_path, {"x": _fps(known)})

    result = run_one(cfg, cfg.checks["x"], extra=[])
    assert result.digest["baseline"] == {"known": 2, "new": 2}
    # classifying findings does not disturb the run's provenance document
    meta = json.loads((result.run_dir / META_NAME).read_text(encoding="utf-8"))
    assert meta["check"] == "x" and meta["parser"] == "fp"

    # A check with no findings at all writes no `findings` key; classifying
    # that digest must still work.
    class _Empty:
        name = "fp"

        def parse(self, ctx: object) -> ParseResult:
            return ParseResult(parser_ok=True)

    monkeypatch.setattr(app_run, "get_parser", lambda _n: _Empty())
    _stub_execute(monkeypatch, rc=0)
    empty = run_one(cfg, cfg.checks["x"], extra=[]).digest
    assert "findings" not in empty and "baseline" not in empty
    assert empty["status"] == "pass" and empty["gate"]["status"] == "pass"


def test_baseline_never_masks_a_parse_mismatch_a_confident_parser_produced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rc 0 with findings is untrusted evidence even when the parser was sure."""
    cfg = _cfg_with_baseline(tmp_path)
    finding = Finding(id="F", kind="k", message="m")
    monkeypatch.setattr(app_run, "get_parser", lambda _n: _finding_parser(finding))
    _stub_execute(monkeypatch, rc=0)
    assert cfg.baseline_path is not None
    save(cfg.baseline_path, {"x": _fps([finding])})

    digest = run_one(cfg, cfg.checks["x"], extra=[]).digest
    # The finding is accepted and the parser was confident, so the only thing
    # standing between this run and a green gate is the execution status.
    assert digest["baseline"] == {"known": 1, "new": 0}
    assert digest["status"] == "parse_mismatch"
    assert digest["gate"]["status"] == "unavailable"


def test_baseline_gate_unavailable_when_evidence_untrusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg_with_baseline(tmp_path)
    finding = Finding(id="F", kind="k", message="m")
    # rc 0 but parser could not interpret output -> parse_mismatch
    monkeypatch.setattr(
        app_run, "get_parser", lambda _n: _finding_parser(finding, parser_ok=False)
    )
    _stub_execute(monkeypatch, rc=0)
    # baseline already contains the finding
    assert cfg.baseline_path is not None
    save(cfg.baseline_path, {"x": _fps([finding])})

    digest = run_one(cfg, cfg.checks["x"], extra=[]).digest
    assert digest["status"] == "parse_mismatch"
    assert digest["gate"]["status"] == "unavailable"  # baseline never masks this


def test_no_baseline_config_means_no_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "ckdn.toml").write_text(
        '[run]\nruns_dir = ".agent-runs"\n[check.x]\ncommand = "cmd"\nparser = "fp"\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path / "ckdn.toml", cwd=tmp_path)
    finding = Finding(id="F", kind="k", message="m")
    monkeypatch.setattr(app_run, "get_parser", lambda _n: _finding_parser(finding))
    _stub_execute(monkeypatch, rc=1)
    digest = run_one(cfg, cfg.checks["x"], extra=[]).digest
    assert "gate" not in digest and "baseline" not in digest


def test_an_alias_reports_one_gate_for_all_its_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The aggregate needs its own gate, or `run --gate` on an alias is blind."""
    (tmp_path / "ckdn.toml").write_text(
        '[run]\nruns_dir = ".agent-runs"\nbaseline = "b.json"\n'
        '[check.x]\ncommand = "cmd"\nparser = "fp"\n'
        '[check.y]\ncommand = "cmd"\nparser = "fp"\n'
        '[check.both]\nmembers = ["x", "y"]\nfail_fast = false\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path / "ckdn.toml", cwd=tmp_path)
    finding = Finding(id="F", kind="k", message="m", location="a.py:5")
    monkeypatch.setattr(app_run, "get_parser", lambda _n: _finding_parser(finding))
    _stub_execute(monkeypatch, rc=1)

    # x has accepted the finding, y has not.
    assert cfg.baseline_path is not None
    save(cfg.baseline_path, {"x": _fps([finding])})

    aggregate = run_alias(cfg, cfg.checks["both"]).aggregate
    assert aggregate["status"] == "fail"
    # One member still carries a new finding, so the combined gate fails.
    assert aggregate["gate"]["status"] == "fail"


# --- a gate is only ever `pass` on evidence the baseline classified ---------


def _gate_failure_parser() -> object:
    """A coverage-shaped parser: a policy gate breach and zero findings."""

    class _FP:
        name = "fp"

        def parse(self, ctx: object) -> ParseResult:
            return ParseResult(
                gate_failures=["line coverage 80.0% is below fail_under=95.0%"]
            )

    return _FP()


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "ckdn.toml"
    path.write_text(
        '[run]\nruns_dir = ".agent-runs"\nbaseline = "b.json"\n' + body,
        encoding="utf-8",
    )
    return path


def test_an_rc_only_failure_is_never_gated_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`parser = "generic"` produces no findings by construction.

    A failed build therefore reached the gate with ``new == 0`` and was read as
    "no new findings" — `--gate` exited 0 on a genuinely failed check.
    """
    path = _write_config(tmp_path, '[check.x]\ncommand = "cmd"\nparser = "generic"\n')
    cfg = load_config(path, cwd=tmp_path)
    _stub_execute(monkeypatch, rc=1)

    result = run_one(cfg, cfg.checks["x"], extra=[])
    digest = result.digest
    assert digest["status"] == "fail" and digest["rc"] == 1  # execution truth
    assert "findings" not in digest and "baseline" not in digest
    assert digest["gate"]["status"] == "unavailable"
    assert gate_exit(digest["gate"]["status"], result.exit_code) == 1
    Draft202012Validator(load_schema(DIGEST_SCHEMA)).validate(digest)


def test_a_policy_gate_failure_is_never_gated_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A coverage `fail_under` breach has no fingerprint to baseline.

    The digest used to carry `gate_failures` and `gate.status == "pass"` at the
    same time — one document contradicting itself.
    """
    path = _write_config(tmp_path, '[check.x]\ncommand = "cmd"\nparser = "fp"\n')
    cfg = load_config(path, cwd=tmp_path)
    monkeypatch.setattr(app_run, "get_parser", lambda _n: _gate_failure_parser())
    _stub_execute(monkeypatch, rc=0)

    result = run_one(cfg, cfg.checks["x"], extra=[])
    digest = result.digest
    assert digest["status"] == "fail"  # execution truth, unchanged
    assert digest["gate_failures"] == ["line coverage 80.0% is below fail_under=95.0%"]
    assert digest["gate"]["status"] == "fail"
    assert gate_exit(digest["gate"]["status"], result.exit_code) == 1
    Draft202012Validator(load_schema(DIGEST_SCHEMA)).validate(digest)


def test_gate_flag_does_not_exit_zero_on_an_unaccounted_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user-visible symptom: `ckdn run --gate` exited 0 on a red build."""
    path = _write_config(tmp_path, '[check.x]\ncommand = "cmd"\nparser = "generic"\n')
    _stub_execute(monkeypatch, rc=1)
    argv = ["run", "x", "--config", str(path), "--cwd", str(tmp_path), "--quiet"]
    assert cli.main([*argv, "--gate"]) == 1
    assert cli.main(argv) == 1  # execution exit, unchanged


def test_an_alias_does_not_gate_pass_on_an_unaccounted_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The aggregate gate inherits the member decisions, so it cannot launder one."""
    path = _write_config(
        tmp_path,
        '[check.x]\ncommand = "cmd"\nparser = "generic"\n'
        '[check.y]\ncommand = "cmd"\nparser = "generic"\n'
        '[check.both]\nmembers = ["x", "y"]\nfail_fast = false\n',
    )
    cfg = load_config(path, cwd=tmp_path)
    _stub_execute(monkeypatch, rc=1)

    result = run_alias(cfg, cfg.checks["both"])
    assert result.aggregate["status"] == "fail"
    assert result.aggregate["gate"]["status"] == "unavailable"
    assert gate_exit(result.aggregate["gate"]["status"], result.exit_code) == 1
