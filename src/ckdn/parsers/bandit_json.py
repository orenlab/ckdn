# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""bandit parser backed by the JSON report written to a file.

Expected command shape:

    uv run bandit -r src -f json -o {run_dir}/bandit.json

Severity filtering is done TOOL-SIDE (``--severity-level``), never here —
hiding findings the exit code knows about would manufacture a parse_mismatch.
"""

from __future__ import annotations

from typing import Any

from ckdn.parsers.base import (
    Finding,
    ParseContext,
    ParseResult,
    load_json_artifact,
    top_counts,
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
        self._verify_metrics(result, findings, metrics)
        return result

    @staticmethod
    def _verify_metrics(
        result: ParseResult,
        findings: list[Finding],
        metrics: dict[str, Any],
    ) -> None:
        """Cross-check findings against metrics._totals, totals, or per-file maps."""
        if not metrics:
            return
        # Prefer aggregate totals; otherwise sum per-file severity maps.
        totals = metrics.get("_totals", metrics.get("totals"))
        if isinstance(totals, dict):
            declared = 0
            for key, raw in totals.items():
                key_s = str(key).upper()
                if "SEVERITY" in key_s and isinstance(raw, (int, float)):
                    declared += int(raw)
            if declared and declared != len(findings):
                result.parser_ok = False
                result.notes.append(
                    f"bandit metrics imply {declared} issue(s) but "
                    f"{len(findings)} were parsed; refusing to trust this parse"
                )
            return
        # Per-file metrics: sum HIGH/MEDIUM/LOW/UNDEFINED across files.
        declared = 0
        for value in metrics.values():
            if not isinstance(value, dict):
                continue
            for key in (
                "SEVERITY.HIGH",
                "SEVERITY.MEDIUM",
                "SEVERITY.LOW",
                "SEVERITY.UNDEFINED",
            ):
                raw = value.get(key)
                if isinstance(raw, (int, float)):
                    declared += int(raw)
        if declared and declared != len(findings):
            result.parser_ok = False
            result.notes.append(
                f"bandit metrics imply {declared} issue(s) but "
                f"{len(findings)} were parsed; refusing to trust this parse"
            )
