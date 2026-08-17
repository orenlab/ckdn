# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""bandit parser backed by the JSON report written to a file.

Expected command shape:

    uv run bandit -r src -f json -o {run_dir}/bandit.json

Severity filtering is done TOOL-SIDE (``--severity-level``), never here —
hiding findings the exit code knows about would manufacture a parse_mismatch.

The metrics cross-check has to know that, though. Verified against bandit
1.9.4: ``--severity-level`` / ``--confidence-level`` filter ``results`` but
NOT ``metrics`` — both ``_totals`` and the per-file maps keep counting every
issue the scan found. Summing all severity buckets and comparing that to the
number of parsed findings therefore fires on every filtered run, which turns
the flag this module recommends into a permanent ``parse_mismatch``. See
:meth:`BanditJsonParser._verify_metrics` for what is actually inferable.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ckdn.parsers.base import (
    Finding,
    ParseContext,
    ParseResult,
    load_json_artifact,
    top_counts,
)

#: bandit's severity/confidence ladder, lowest rank first (``bandit.core.RANKING``).
_RANKS = ("UNDEFINED", "LOW", "MEDIUM", "HIGH")

#: The two filterable axes: ``(metrics bucket prefix, results field)``.
_AXES = (("SEVERITY", "issue_severity"), ("CONFIDENCE", "issue_confidence"))


def _bucket_totals(metrics: dict[str, Any]) -> dict[str, dict[int, int]]:
    """Aggregate the ``SEVERITY.*`` / ``CONFIDENCE.*`` buckets by rank.

    Prefers the aggregate ``_totals`` table (``totals`` is accepted as an
    alias); when neither is a table, the per-file maps are summed instead.
    """
    totals = metrics.get("_totals", metrics.get("totals"))
    sources = (
        [totals]
        if isinstance(totals, dict)
        else [value for value in metrics.values() if isinstance(value, dict)]
    )
    counts: dict[str, dict[int, int]] = {axis: {} for axis, _ in _AXES}
    for source in sources:
        for axis, _ in _AXES:
            for rank, name in enumerate(_RANKS):
                raw = source.get(f"{axis}.{name}")
                if isinstance(raw, (int, float)):
                    counts[axis][rank] = counts[axis].get(rank, 0) + int(raw)
    return counts


def _observed_floor(results: Sequence[Any], field: str) -> int | None:
    """Lowest rank ``results`` actually shows on one axis, or ``None``.

    This is the only handle the report gives on tool-side filtering: a
    ``--severity-level``/``--confidence-level`` cut keeps a suffix of the
    ladder, so the lowest rank still present bounds where the cut was.
    """
    floor: int | None = None
    for item in results:
        if not isinstance(item, dict):
            continue
        name = str(item.get(field) or "").upper()
        if name not in _RANKS:
            continue
        rank = _RANKS.index(name)
        if floor is None or rank < floor:
            floor = rank
    return floor


def _refuse(result: ParseResult, declared: int, parsed: int) -> None:
    """Flag the parse as untrustworthy: metrics and findings contradict."""
    result.parser_ok = False
    result.notes.append(
        f"bandit metrics imply {declared} issue(s) but "
        f"{parsed} were parsed; refusing to trust this parse"
    )


def _skip(result: ParseResult, declared_total: int) -> None:
    """Record that the cross-check abstained, and why."""
    result.notes.append(
        f"bandit metrics count {declared_total} issue(s) that `results` does "
        "not list; tool-side --severity-level/--confidence-level filtering is "
        "invisible in metrics, so the metrics cross-check was skipped"
    )


class BanditJsonParser:
    name = "bandit"

    def parse(self, ctx: ParseContext) -> ParseResult:
        report = load_json_artifact(
            ctx,
            default_name="bandit.json",
            tool="bandit",
            expect=dict,
            missing_hint=(
                "The check command must include `-f json -o {run_dir}/bandit.json`."
            ),
            nested_key="results",
            nested_expect=list,
            nested_missing_note=(
                "bandit report missing `results` array; unexpected format"
            ),
        )
        if report[1] is not None:
            return report[1]
        root, results = report[0]

        findings: list[Finding] = []
        by_severity: dict[str, int] = {}
        by_test_id: dict[str, int] = {}
        for item in results:
            if not isinstance(item, dict):
                continue
            test_id = str(item.get("test_id") or "?")
            filename = str(item.get("filename") or "?")
            line = item.get("line_number")
            location = f"{filename}:{line}" if line is not None else filename
            severity = str(item.get("issue_severity") or "?").lower()
            confidence = str(item.get("issue_confidence") or "?")
            cwe = item.get("issue_cwe") or {}
            cwe_id = ""
            if isinstance(cwe, dict):
                cwe_id = str(cwe.get("id") or "")
            by_severity[severity] = by_severity.get(severity, 0) + 1
            by_test_id[test_id] = by_test_id.get(test_id, 0) + 1
            detail = [
                f"severity={severity}",
                f"confidence={confidence}",
            ]
            if cwe_id:
                detail.append(f"CWE-{cwe_id}")
            findings.append(
                Finding(
                    id=f"{test_id} {location}",
                    kind="security_issue",
                    message=str(item.get("issue_text") or "")[:400],
                    location=location,
                    detail=tuple(detail),
                )
            )

        raw_metrics = root.get("metrics")
        metrics: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}
        result = ParseResult(
            findings=findings,
            summary={
                "issue_count": len(findings),
                "by_severity": top_counts(by_severity, ctx.top),
                "by_test_id": top_counts(by_test_id, ctx.top),
            },
        )
        self._verify_metrics(result, findings, metrics, results, ctx.rc)
        return result

    @staticmethod
    def _verify_metrics(
        result: ParseResult,
        findings: list[Finding],
        metrics: dict[str, Any],
        results: Sequence[Any],
        rc: int,
    ) -> None:
        """Cross-check parsed findings against the metrics tables.

        The guard exists to catch a parse that silently dropped findings, and
        it stays. What changes is what counts as "declared", because metrics
        are never filtered while ``results`` is:

        * Empty ``results`` with ``rc == 0`` -- bandit found nothing at or
          above its configured level and said so. The parse took zero entries
          and produced zero findings, so it cannot have lost anything; the
          leftover metrics are the filter, not a parse fault. Abstain.
          ``rc != 0`` means bandit did find reportable issues, so an empty
          ``results`` is not filtering and the guard still fires.
        * Otherwise, count only ranks at or above the floor ``results`` shows
          on each axis. Under a severity cut that sum is exactly the surviving
          issue count, so the guard keeps its full strength.
        * When EVERY usable axis shows counts below its floor, all of them were
          cut and the per-axis marginals cannot reconstruct the joint count.
          Nothing is inferable, so abstain rather than guess.
        """
        if not metrics:
            return
        counts = _bucket_totals(metrics)
        declared_total = sum(counts["SEVERITY"].values())
        if not declared_total:
            return

        if not results:
            if rc == 0:
                _skip(result, declared_total)
                return
            _refuse(result, declared_total, len(findings))
            return

        bounds: list[int] = []
        uncut = False
        for axis, field in _AXES:
            floor = _observed_floor(results, field)
            if floor is None or not counts[axis]:
                continue
            below = sum(n for rank, n in counts[axis].items() if rank < floor)
            bounds.append(sum(counts[axis].values()) - below)
            if not below:
                uncut = True
        # No usable axis, or every usable axis was cut: nothing is inferable.
        if not uncut:
            _skip(result, declared_total)
            return

        declared = min(bounds)
        if declared != len(findings):
            _refuse(result, declared, len(findings))
