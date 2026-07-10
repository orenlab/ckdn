# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""ruff parser backed by the JSON report written to a file.

The command must direct the JSON report into the run directory so the
report is never polluted by other stdout noise (uv, warnings, etc.):

    uv run ruff check --output-format json --output-file {run_dir}/ruff.json .

ruff exit codes: 0 clean, 1 violations found, 2 tool error.
"""

from __future__ import annotations

import json

from ckdn.parsers.base import Finding, ParseContext, ParseResult


class RuffJsonParser:
    name = "ruff"

    def parse(self, ctx: ParseContext) -> ParseResult:
        report_path = ctx.artifact("report", "ruff.json")
        if not report_path.exists():
            return ParseResult(
                parser_ok=False,
                notes=[
                    f"ruff JSON report not found: {report_path}. The check "
                    "command must include `--output-format json "
                    "--output-file {run_dir}/ruff.json`. rc == 2 usually "
                    "means ruff itself failed -- see log_tail."
                ],
            )
        try:
            data = json.loads(report_path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            return ParseResult(
                parser_ok=False,
                notes=[f"ruff report is not valid JSON: {exc}"],
            )
        if not isinstance(data, list):
            return ParseResult(
                parser_ok=False,
                notes=["ruff report is not a JSON array; unexpected format"],
            )

        findings: list[Finding] = []
        by_code: dict[str, int] = {}
        fixable = 0
        for item in data:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "?")
            filename = str(item.get("filename") or "?")
            loc = item.get("location") or {}
            row, col = loc.get("row"), loc.get("column")
            location = f"{filename}:{row}:{col}" if row is not None else filename
            by_code[code] = by_code.get(code, 0) + 1
            if item.get("fix"):
                fixable += 1
            findings.append(
                Finding(
                    id=f"{location} {code}",
                    kind="lint_violation",
                    message=str(item.get("message") or "")[:400],
                    location=location,
                )
            )

        return ParseResult(
            findings=findings,
            summary={
                "violation_count": len(findings),
                "fixable_count": fixable,
                "by_code": dict(sorted(by_code.items())),
            },
        )
