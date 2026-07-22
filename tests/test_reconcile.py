# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Contract tests for the status model. If these fail, nothing else matters."""

from ckdn.parsers.base import Finding, ParseResult
from ckdn.reconcile import reconcile


def _finding() -> Finding:
    return Finding(id="tests.test_x::test_y", kind="test_failure", message="boom")


def test_interrupted_outranks_every_other_signal() -> None:
    """Partial evidence from a cut-short run is never a verdict."""
    # a half-written report must not read as `fail`
    status, reason, tail = reconcile(
        1, ParseResult(findings=[_finding()]), interrupted=True
    )
    assert status == "error" and "interrupted" in reason and tail is True
    # nor as `parse_mismatch` when the parser could not read the partial file
    assert reconcile(0, ParseResult(parser_ok=False), interrupted=True)[0] == "error"
    # nor as a gate failure
    assert (
        reconcile(0, ParseResult(gate_failures=["coverage too low"]), interrupted=True)[
            0
        ]
        == "error"
    )
    # and it is never green
    assert reconcile(0, ParseResult(), interrupted=True)[0] == "error"


def test_green_requires_rc_zero_and_clean_parse() -> None:
    status, _, tail = reconcile(0, ParseResult())
    assert status == "pass"
    assert tail is False


def test_nonzero_with_findings_is_fail() -> None:
    status, _, _ = reconcile(1, ParseResult(findings=[_finding()]))
    assert status == "fail"


def test_nonzero_without_findings_is_error_not_fail() -> None:
    """A red exit code without evidence means infra failure, not 'tests failed'."""
    status, _, tail = reconcile(2, ParseResult())
    assert status == "error"
    assert tail is True


def test_rc_zero_with_findings_is_mismatch() -> None:
    """Text evidence may never be silently discarded when the rc looks green."""
    status, _, _ = reconcile(0, ParseResult(findings=[_finding()]))
    assert status == "parse_mismatch"


def test_parser_not_ok_never_yields_green_on_rc_zero() -> None:
    status, _, _ = reconcile(0, ParseResult(parser_ok=False))
    assert status == "parse_mismatch"


def test_parser_not_ok_on_failure_is_error() -> None:
    status, _, _ = reconcile(3, ParseResult(parser_ok=False))
    assert status == "error"


def test_gate_failure_overrides_green_rc() -> None:
    """Coverage below fail_under must fail even when pytest exits 0."""
    status, reason, _ = reconcile(
        0,
        ParseResult(gate_failures=["line coverage 80.0% is below fail_under=95.0%"]),
    )
    assert status == "fail"
    assert "fail_under" in reason


def test_generic_checks_fail_without_evidence() -> None:
    """evidence_expected=False downgrades error -> fail for rc-only checks."""
    status, _, tail = reconcile(3, ParseResult(evidence_expected=False))
    assert status == "fail"
    assert tail is True
