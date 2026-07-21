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
from typing import Any

import pytest
from digest_factory import make_digest, make_finding, make_outcome
from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

from ckdn import AGGREGATE_SCHEMA, DIGEST_SCHEMA, META_SCHEMA, cli
from ckdn.digest import build_alias_aggregate, build_meta
from ckdn.parsers.base import ParseResult
from ckdn.runner import RC_TIMEOUT
from ckdn.schema import SCHEMA_FILES, load_schema, schema_ids


def _validator(schema_id: str) -> Validator:
    schema = load_schema(schema_id)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


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
    digest = make_digest(0, ParseResult(parser_ok=True))
    _validator(DIGEST_SCHEMA).validate(digest)
    assert digest["status"] == "pass"
    assert "status_reason" not in digest


def test_all_digest_variants_validate() -> None:
    validator = _validator(DIGEST_SCHEMA)
    variants: dict[str, dict[str, Any]] = {
        "pass": make_digest(0, ParseResult(parser_ok=True)),
        "pass_with_summary": make_digest(
            0, ParseResult(parser_ok=True, summary={"counts": {"tests": 10}})
        ),
        "fail_findings": make_digest(
            1,
            ParseResult(parser_ok=True, findings=[make_finding(1)]),
            artifacts=["full.log", "junit.xml"],
        ),
        "fail_gate": make_digest(
            0, ParseResult(parser_ok=True, gate_failures=["coverage 90.0% < 96.0%"])
        ),
        "error_no_evidence": make_digest(2, ParseResult(parser_ok=True)),
        "error_parser_broke": make_digest(
            2, ParseResult(parser_ok=False, notes=["could not parse"])
        ),
        "parse_mismatch_rc0_findings": make_digest(
            0, ParseResult(parser_ok=True, findings=[make_finding(1)])
        ),
        "parse_mismatch_rc0_unreadable": make_digest(0, ParseResult(parser_ok=False)),
        "timed_out": make_digest(
            RC_TIMEOUT, ParseResult(parser_ok=True), timed_out=True
        ),
        "truncated": make_digest(
            1,
            ParseResult(parser_ok=True, findings=[make_finding(i) for i in range(3)]),
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
            ("format", "pass", 0, ".agent-runs/x-format"),
            ("ruff", "pass", 0, ".agent-runs/x-ruff"),
        ],
        status="pass",
        rc=0,
    )
    mixed = build_alias_aggregate(
        alias="lint",
        results=[
            ("ruff", "pass", 0, ".agent-runs/x-ruff"),
            ("pylint", "fail", 1, ".agent-runs/x-pylint"),
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
    meta = build_meta(check="pytest", parser="pytest", outcome=make_outcome(1))
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
