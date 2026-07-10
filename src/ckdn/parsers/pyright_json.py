# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""pyright parser over ``--outputjson`` stdout (extracted from the log).

Expected command shape:

    uvx pyright --outputjson

CAVEAT: the archived log is interleaved stdout+stderr, so node/npm noise may
surround the JSON. We extract from the first ``{`` to the last ``}``.

Warnings are summary-only: pyright exits 0 on warnings-only, and
warning-findings would trip the reconciler mismatch guard.
"""

from __future__ import annotations

import json
from typing import Any

from ckdn.parsers.base import Finding, ParseContext, ParseResult, format_location


def _extract_json_object(text: str) -> Any | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _diagnostic_location(item: dict[str, Any]) -> str:
    file_path = str(item.get("file") or "?")
    rng = item.get("range") or {}
    start = rng.get("start") if isinstance(rng, dict) else {}
    if not isinstance(start, dict):
        return file_path
    line = start.get("line")
    col = start.get("character")
    # pyright uses 0-based line/col; surface 1-based for humans.
    if isinstance(line, int) and isinstance(col, int):
        return format_location(file_path, line + 1, col + 1)
    return file_path


def _error_finding(item: dict[str, Any]) -> Finding:
    location = _diagnostic_location(item)
    rule = str(item.get("rule") or "")
    finding_id = f"{location} {rule}".strip() if rule else location
    return Finding(
        id=finding_id,
        kind="type_error",
        message=str(item.get("message") or "")[:400],
        location=location,
    )


def _ingest_diagnostics(
    diagnostics: list[Any],
) -> tuple[list[Finding], int, int]:
    findings: list[Finding] = []
    error_count = 0
    warning_count = 0
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "").lower()
        if severity == "warning":
            warning_count += 1
        elif severity == "error":
            error_count += 1
            findings.append(_error_finding(item))
    return findings, error_count, warning_count


class PyrightJsonParser:
    name = "pyright"

    def parse(self, ctx: ParseContext) -> ParseResult:
        data = _extract_json_object(ctx.log_text)
        if data is None:
            return ParseResult(
                parser_ok=False,
                notes=[
                    "could not extract a JSON object from the pyright log "
                    "(expected `--outputjson` on stdout); inspect log_tail"
                ],
            )
        if not isinstance(data, dict):
            return ParseResult(
                parser_ok=False,
                notes=["pyright JSON root is not an object; unexpected format"],
            )
        diagnostics = data.get("generalDiagnostics")
        if not isinstance(diagnostics, list):
            return ParseResult(
                parser_ok=False,
                notes=[
                    "pyright JSON missing `generalDiagnostics` array; unexpected format"
                ],
            )
        raw_summary = data.get("summary")
        summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}

        findings, error_count, warning_count = _ingest_diagnostics(diagnostics)
        result = ParseResult(
            findings=findings,
            summary={
                "error_count": error_count,
                "warning_count": warning_count,
            },
        )
        declared_errors = summary.get("errorCount")
        declared_warnings = summary.get("warningCount")
        if declared_errors is not None and int(declared_errors) != error_count:
            result.parser_ok = False
            result.notes.append(
                f"pyright summary.errorCount={declared_errors} but "
                f"{error_count} error diagnostic(s) were parsed"
            )
        elif declared_warnings is not None and int(declared_warnings) != warning_count:
            result.parser_ok = False
            result.notes.append(
                f"pyright summary.warningCount={declared_warnings} but "
                f"{warning_count} warning diagnostic(s) were parsed"
            )
        return result
