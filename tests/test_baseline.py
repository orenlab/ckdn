# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Finding baselines: three axes (execution / findings / gate).

The invariant under test: baseline never changes execution truth. It only
classifies findings and derives a separate gate that may accept a nonzero exit
for CI — but only when the evidence is trustworthy.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ckdn import DIGEST_SCHEMA
from ckdn.app import run as app_run
from ckdn.app import run_one
from ckdn.baseline import (
    combine_gate,
    fingerprint,
    fingerprints_for,
    gate,
    gate_exit,
    load,
    save,
)
from ckdn.config import Config, load_config
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
    assert gate("fail", True, 0)["status"] == "pass"
    assert gate("fail", True, 3)["status"] == "fail"
    # untrustworthy evidence is never accepted by baseline
    assert gate("error", True, 0)["status"] == "unavailable"
    assert gate("parse_mismatch", True, 0)["status"] == "unavailable"
    assert gate("fail", False, 0)["status"] == "unavailable"


def test_gate_exit() -> None:
    assert gate_exit("pass", 1) == 0
    assert gate_exit("fail", 1) == 1
    assert gate_exit("unavailable", 4) == 4  # honest execution exit
    assert gate_exit(None, 7) == 7


def test_combine_gate() -> None:
    assert combine_gate([]) is None
    passes = combine_gate([{"gate": {"status": "pass"}}, {"gate": {"status": "pass"}}])
    assert passes is not None and passes["status"] == "pass"
    mixed = combine_gate([{"gate": {"status": "pass"}}, {"gate": {"status": "fail"}}])
    assert mixed is not None and mixed["status"] == "fail"
    worst = combine_gate(
        [{"gate": {"status": "fail"}}, {"gate": {"status": "unavailable"}}]
    )
    assert worst is not None and worst["status"] == "unavailable"


# --- integration: execution truth is preserved ----------------------------


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
    save(cfg.baseline_path, {"x": fingerprints_for("x", [finding.to_dict()])})

    # same finding is now known: execution truth UNCHANGED, gate passes
    second = run_one(cfg, cfg.checks["x"], extra=[]).digest
    assert second["status"] == "fail" and second["rc"] == 1  # never upgraded
    assert second["gate"]["status"] == "pass"
    assert second["baseline"] == {"known": 1, "new": 0}
    assert second["findings"][0]["baselined"] is True
    # the digest with baseline/gate still conforms to the published schema
    Draft202012Validator(load_schema(DIGEST_SCHEMA)).validate(second)


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
    save(cfg.baseline_path, {"x": fingerprints_for("x", [finding.to_dict()])})

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
