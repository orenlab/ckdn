# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Shared builders for digest/meta fixtures.

Kept in one place so the schema-conformance and determinism suites construct
documents the same way instead of duplicating ``RunOutcome`` / ``build_digest``
wiring. Not a test module (no ``test_`` prefix), so pytest does not collect it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ckdn.digest import build_digest
from ckdn.parsers.base import Finding, ParseResult
from ckdn.reconcile import reconcile
from ckdn.runner import RunOutcome

RUN_DIR_REL = ".agent-runs/20260101T000000Z-x"


def make_outcome(
    rc: int, *, timed_out: bool = False, interrupted: bool = False
) -> RunOutcome:
    return RunOutcome(
        run_dir=Path(RUN_DIR_REL),
        tokens=["tool", "--flag"],
        rc=rc,
        log_text="line one\nline two\n",
        started_at="2026-01-01T00:00:00+00:00",
        duration_s=0.0,
        timed_out=timed_out,
        exec_note=None,
        interrupted=interrupted,
    )


def make_finding(n: int) -> Finding:
    return Finding(
        id=f"tests.test_mod::test_case_{n}",
        kind="test_failure",
        message="assert 1 == 2",
        location=f"tests/test_mod.py:{n}",
        detail=("E   assert 1 == 2",),
    )


def make_digest(
    rc: int,
    result: ParseResult,
    *,
    timed_out: bool = False,
    interrupted: bool = False,
    top: int = 20,
    artifacts: list[str] | None = None,
) -> dict[str, Any]:
    outcome = make_outcome(rc, timed_out=timed_out, interrupted=interrupted)
    status, reason, include_tail = reconcile(rc, result, interrupted=interrupted)
    return build_digest(
        check="pytest",
        status=status,
        reason=reason,
        outcome=outcome,
        result=result,
        run_dir_rel=RUN_DIR_REL,
        top=top,
        include_tail=include_tail,
        tail_lines=40,
        artifacts=artifacts or [],
    )
