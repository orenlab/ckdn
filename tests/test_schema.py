# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""ckdn emits documents that conform to its own published JSON Schemas.

This is the enforcement behind the ``machine-readable contract`` claim: every
status variant of ``ckdn.digest/2``, every ``ckdn.aggregate/1`` shape, and
``ckdn.meta/1`` are built here and validated against the packaged schema. A
structural drift (renamed/added/removed key) fails this test loudly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

from ckdn import AGGREGATE_SCHEMA, DIGEST_SCHEMA, META_SCHEMA, cli
from ckdn.digest import build_alias_aggregate, build_digest, build_meta
from ckdn.parsers.base import Finding, ParseResult
from ckdn.reconcile import reconcile
from ckdn.runner import RC_TIMEOUT, RunOutcome
from ckdn.schema import SCHEMA_FILES, load_schema, schema_ids


def _validator(schema_id: str) -> Validator:
    schema = load_schema(schema_id)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _outcome(rc: int, *, timed_out: bool = False) -> RunOutcome:
    return RunOutcome(
        run_dir=Path(".agent-runs/20260101T000000Z-x"),
        tokens=["tool", "--flag"],
        rc=rc,
        log_text="line one\nline two\n",
        started_at="2026-01-01T00:00:00+00:00",
        duration_s=0.0,
        timed_out=timed_out,
        exec_note=None,
    )


def _digest(
    rc: int,
    result: ParseResult,
    *,
    timed_out: bool = False,
    top: int = 20,
    artifacts: list[str] | None = None,
) -> dict[str, Any]:
    outcome = _outcome(rc, timed_out=timed_out)
    status, reason, include_tail = reconcile(rc, result)
    return build_digest(
        check="pytest",
        status=status,
        reason=reason,
        outcome=outcome,
        result=result,
        run_dir_rel=".agent-runs/20260101T000000Z-x",
        top=top,
        include_tail=include_tail,
        tail_lines=40,
        artifacts=artifacts or [],
    )


def _finding(n: int) -> Finding:
    return Finding(
        id=f"tests.test_mod::test_case_{n}",
        kind="test_failure",
        message="assert 1 == 2",
        location=f"tests/test_mod.py:{n}",
        detail=("E   assert 1 == 2",),
    )


# --- packaged schema sanity ------------------------------------------------


def test_schema_ids_cover_every_emitted_schema() -> None:
    assert set(schema_ids()) == {DIGEST_SCHEMA, AGGREGATE_SCHEMA, META_SCHEMA}


@pytest.mark.parametrize("schema_id", sorted(SCHEMA_FILES))
def test_packaged_schemas_are_valid(schema_id: str) -> None:
    schema = load_schema(schema_id)
    Draft202012Validator.check_schema(schema)
    assert schema["title"] == schema_id


def test_load_schema_rejects_unknown_id() -> None:
    with pytest.raises(ValueError, match="no packaged schema"):
        load_schema("ckdn.nope/9")


# --- digest variants -------------------------------------------------------


def test_pass_digest_validates_and_is_minimal() -> None:
    digest = _digest(0, ParseResult(parser_ok=True))
    _validator(DIGEST_SCHEMA).validate(digest)
    assert digest["status"] == "pass"
    assert "status_reason" not in digest


def test_all_digest_variants_validate() -> None:
    validator = _validator(DIGEST_SCHEMA)
    variants: dict[str, dict[str, Any]] = {
        "pass": _digest(0, ParseResult(parser_ok=True)),
        "pass_with_summary": _digest(
            0, ParseResult(parser_ok=True, summary={"counts": {"tests": 10}})
        ),
        "fail_findings": _digest(
            1,
            ParseResult(parser_ok=True, findings=[_finding(1)]),
            artifacts=["full.log", "junit.xml"],
        ),
        "fail_gate": _digest(
            0, ParseResult(parser_ok=True, gate_failures=["coverage 90.0% < 96.0%"])
        ),
        "error_no_evidence": _digest(2, ParseResult(parser_ok=True)),
        "error_parser_broke": _digest(
            2, ParseResult(parser_ok=False, notes=["could not parse"])
        ),
        "parse_mismatch_rc0_findings": _digest(
            0, ParseResult(parser_ok=True, findings=[_finding(1)])
        ),
        "parse_mismatch_rc0_unreadable": _digest(0, ParseResult(parser_ok=False)),
        "timed_out": _digest(RC_TIMEOUT, ParseResult(parser_ok=True), timed_out=True),
        "truncated": _digest(
            1,
            ParseResult(parser_ok=True, findings=[_finding(i) for i in range(3)]),
            top=1,
        ),
    }
    seen_status: set[str] = set()
    for digest in variants.values():
        validator.validate(digest)  # raises on non-conformance
        seen_status.add(digest["status"])
    # every status in the model is exercised
    assert seen_status == {"pass", "fail", "error", "parse_mismatch"}
    assert variants["timed_out"]["timed_out"] is True
    assert variants["truncated"]["findings_truncated"] == 2


# --- aggregate variants ----------------------------------------------------


def test_aggregate_variants_validate() -> None:
    validator = _validator(AGGREGATE_SCHEMA)
    all_pass = build_alias_aggregate(
        alias="style",
        results=[
            ("format", "pass", 0, Path(".agent-runs/x-format")),
            ("ruff", "pass", 0, Path(".agent-runs/x-ruff")),
        ],
        status="pass",
        rc=0,
    )
    mixed = build_alias_aggregate(
        alias="lint",
        results=[
            ("ruff", "pass", 0, Path(".agent-runs/x-ruff")),
            ("pylint", "fail", 1, Path(".agent-runs/x-pylint")),
        ],
        status="fail",
        rc=1,
    )
    for aggregate in (all_pass, mixed):
        validator.validate(aggregate)
    # passing members carry no run_dir; failing members do
    assert all("run_dir" not in m for m in all_pass["members"])
    assert mixed["members"][0].get("run_dir") is None
    assert "run_dir" in mixed["members"][1]


# --- meta ------------------------------------------------------------------


def test_meta_validates() -> None:
    meta = build_meta(check="pytest", parser="pytest", outcome=_outcome(1))
    _validator(META_SCHEMA).validate(meta)
    assert meta["schema"] == META_SCHEMA


# --- `ckdn schema` CLI -----------------------------------------------------


def test_cli_schema_lists_ids(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["schema"]) == 0
    printed = set(capsys.readouterr().out.split())
    assert printed == {DIGEST_SCHEMA, AGGREGATE_SCHEMA, META_SCHEMA}


def test_cli_schema_prints_valid_schema(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["schema", DIGEST_SCHEMA]) == 0
    schema = json.loads(capsys.readouterr().out)
    Draft202012Validator.check_schema(schema)
    assert schema["title"] == DIGEST_SCHEMA


def test_cli_schema_unknown_id_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["schema", "ckdn.nope/9"]) == 2
    assert "no packaged schema" in capsys.readouterr().err
