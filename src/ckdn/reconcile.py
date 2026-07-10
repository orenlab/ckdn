# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Status reconciliation: exit code x parser result -> final status.

Invariants (the whole point of ckdn):

1. ``pass`` requires BOTH ``rc == 0`` AND a confident parser with no
   findings and no gate failures. Green is a conjunction, never a default.
2. A nonzero exit code can never be upgraded to ``pass`` by parsing.
3. ``rc == 0`` combined with contradicting evidence (findings present, or
   parser unable to interpret the output) yields ``parse_mismatch`` --
   an explicitly non-green state that says "the green signal is untrusted".
4. A failure without parseable evidence is ``error`` (infra/collection
   failure), not ``fail`` -- unless the parser declared that it never
   produces evidence (``evidence_expected=False``, the generic parser).
"""

from __future__ import annotations

from ckdn.parsers.base import ParseResult

#: Every status ckdn can emit. Anything except "pass" is non-green.
STATUSES = ("pass", "fail", "error", "parse_mismatch")


def reconcile(rc: int, result: ParseResult) -> tuple[str, str, bool]:
    """Return ``(status, reason, include_log_tail)``."""
    if not result.parser_ok:
        if rc == 0:
            return (
                "parse_mismatch",
                "exit code 0, but the parser could not interpret the tool "
                "output; the green signal is untrusted",
                True,
            )
        return (
            "error",
            f"exit code {rc} and the parser could not interpret the tool "
            "output; inspect log_tail and full.log",
            True,
        )

    if rc == 0 and result.findings:
        return (
            "parse_mismatch",
            f"exit code 0, but the parser extracted "
            f"{len(result.findings)} failure finding(s)",
            True,
        )

    if result.gate_failures:
        return ("fail", "; ".join(result.gate_failures), result.include_log_tail)

    if rc == 0:
        return ("pass", "exit code 0, no findings, all gates satisfied", False)

    if result.findings:
        return (
            "fail",
            f"exit code {rc} with {len(result.findings)} finding(s)",
            result.include_log_tail,
        )

    if not result.evidence_expected:
        return ("fail", f"exit code {rc}", True)

    return (
        "error",
        f"exit code {rc} without parseable findings; likely an "
        "infrastructure, usage or collection failure -- inspect log_tail",
        True,
    )
