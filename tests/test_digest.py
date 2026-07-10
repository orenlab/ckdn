# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Sparse digest/2 construction and encoding."""

from __future__ import annotations

from pathlib import Path

from ckdn import DIGEST_SCHEMA
from ckdn.digest import (
    build_alias_aggregate,
    build_digest,
    dump_json,
    dump_json_pretty,
    prune_summary,
)
from ckdn.parsers.base import Finding, ParseResult
from ckdn.runner import RunOutcome


def _outcome(*, rc: int = 0, timed_out: bool = False, log: str = "") -> RunOutcome:
    return RunOutcome(
        run_dir=Path("/tmp/run"),
        tokens=["tool"],
        rc=rc,
        log_text=log,
        started_at="2026-01-01T00:00:00+00:00",
        duration_s=0.1,
        timed_out=timed_out,
        exec_note=None,
    )


def test_schema_is_v2() -> None:
    assert DIGEST_SCHEMA == "ckdn.digest/2"


def test_pass_digest_is_minimal() -> None:
    digest = build_digest(
        check="ruff",
        status="pass",
        reason="exit code 0, no findings, all gates satisfied",
        outcome=_outcome(rc=0),
        result=ParseResult(
            summary={"violation_count": 0, "by_code": {}, "fixable_count": 0}
        ),
        run_dir_rel=".agent-runs/x-ruff",
        top=20,
        include_tail=False,
        tail_lines=40,
        artifacts=["full.log", "meta.json", "ruff.json"],
    )
    assert digest == {
        "schema": "ckdn.digest/2",
        "check": "ruff",
        "status": "pass",
        "rc": 0,
        "run_dir": ".agent-runs/x-ruff",
    }
    line = dump_json(digest)
    assert "\n" not in line.strip()
    assert "findings" not in line
    assert "status_reason" not in line


def test_fail_digest_keeps_evidence() -> None:
    finding = Finding(
        id="a",
        kind="lint_violation",
        message="boom",
        location="x.py:1",
        detail=("line1",),
    )
    digest = build_digest(
        check="ruff",
        status="fail",
        reason="exit code 1 with 1 finding(s)",
        outcome=_outcome(rc=1),
        result=ParseResult(
            findings=[finding],
            summary={"violation_count": 1, "by_code": {"E": 1}},
        ),
        run_dir_rel=".agent-runs/x-ruff",
        top=20,
        include_tail=False,
        tail_lines=40,
        artifacts=["full.log", "ruff.json", "meta.json"],
    )
    assert digest["status_reason"] == "exit code 1 with 1 finding(s)"
    assert digest["findings_total"] == 1
    assert digest["findings"] == [
        {
            "id": "a",
            "kind": "lint_violation",
            "message": "boom",
            "location": "x.py:1",
            "detail": ["line1"],
        }
    ]
    assert digest["artifacts"] == ["full.log", "ruff.json", "meta.json"]
    assert "findings_truncated" not in digest
    assert "gate_failures" not in digest
    assert "notes" not in digest
    assert "timed_out" not in digest


def test_coverage_pass_keeps_summary() -> None:
    digest = build_digest(
        check="coverage",
        status="pass",
        reason="ok",
        outcome=_outcome(rc=0),
        result=ParseResult(
            summary={
                "overall": {"line_percent": 96.5, "branch_percent": 0.0},
                "fail_under": 95.0,
                "uncovered_files_total": 0,
                "top_uncovered_files": [],
            }
        ),
        run_dir_rel=".agent-runs/x-cov",
        top=20,
        include_tail=False,
        tail_lines=40,
        artifacts=["coverage.xml", "full.log", "meta.json"],
    )
    assert digest["status"] == "pass"
    assert digest["summary"]["overall"]["line_percent"] == 96.5
    assert digest["summary"]["fail_under"] == 95.0
    assert "artifacts" not in digest


def test_truncation_counters() -> None:
    findings = [Finding(id=str(i), kind="k", message="m") for i in range(5)]
    digest = build_digest(
        check="ruff",
        status="fail",
        reason="many",
        outcome=_outcome(rc=1),
        result=ParseResult(findings=findings),
        run_dir_rel="r",
        top=2,
        include_tail=False,
        tail_lines=10,
        artifacts=["full.log"],
    )
    assert digest["findings_total"] == 5
    assert digest["findings_truncated"] == 3
    assert len(digest["findings"]) == 2


def test_finding_omits_empty_optional_fields() -> None:
    assert Finding(id="a", kind="k", message="m").to_dict() == {
        "id": "a",
        "kind": "k",
        "message": "m",
    }


def test_timed_out_only_when_true() -> None:
    digest = build_digest(
        check="x",
        status="error",
        reason="timeout",
        outcome=_outcome(rc=124, timed_out=True),
        result=ParseResult(notes=["timed out"]),
        run_dir_rel="r",
        top=20,
        include_tail=True,
        tail_lines=2,
        artifacts=["full.log"],
    )
    assert digest["timed_out"] is True
    assert digest["notes"] == ["timed out"]
    assert "log_tail" in digest


def test_prune_summary_drops_zeros() -> None:
    assert prune_summary({"a": 0, "b": {"c": 0}, "d": 2}) == {"d": 2}


def test_alias_aggregate_sparse() -> None:
    agg = build_alias_aggregate(
        alias="lint",
        results=[
            ("ruff", "pass", 0, Path("runs/ruff")),
            ("pylint", "fail", 1, Path("runs/pylint")),
        ],
        status="fail",
        rc=1,
    )
    assert agg == {
        "schema": "ckdn.aggregate/1",
        "alias": "lint",
        "status": "fail",
        "rc": 1,
        "members": [
            {"check": "ruff", "status": "pass", "rc": 0},
            {
                "check": "pylint",
                "status": "fail",
                "rc": 1,
                "run_dir": "runs/pylint",
            },
        ],
    }


def test_pretty_dump_indents() -> None:
    text = dump_json_pretty({"a": 1, "b": 2})
    assert "\n  " in text


def test_tail_nonpositive() -> None:
    from ckdn.digest import tail

    assert tail("a\nb\nc", 0) == []
    assert dump_json({"x": False})  # false omitted by sparse? dump_json doesn't prune
