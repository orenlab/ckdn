# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""mypy parser over terminal text (default) or ``--output json`` NDJSON.

Expected command shapes:

    uv run mypy src
    uv run mypy src --output json   # set format = "json" on the check

Severities: errors → findings; warnings → summary only (mypy can exit 0 with
warnings); notes attach to the preceding error's ``detail``.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field

from ckdn.parsers.base import (
    Finding,
    ParseContext,
    ParseResult,
    clamp,
    format_location,
    top_counts,
)

_LINE_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+)(?::(?P<col>\d+))?:\s*"
    r"(?P<severity>error|warning|note):\s*(?P<message>.*?)"
    r"(?:\s+\[(?P<code>[^\]]+)\])?\s*$"
)
_FOUND_RE = re.compile(r"Found\s+(?P<n>\d+)\s+errors?\s+in\s+(?P<m>\d+)\s+files?")
_CLEAN_RE = re.compile(r"Success:\s*no issues found")


@dataclass
class _JsonAccum:
    findings: list[Finding] = field(default_factory=list)
    warnings: int = 0
    notes: int = 0
    codes: dict[str, int] = field(default_factory=dict)

    def bump_code(self, code: str) -> None:
        if code:
            self.codes[code] = self.codes.get(code, 0) + 1


def _iter_json_objects(log_text: str) -> Iterator[dict[str, object]]:
    for raw in log_text.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            yield item


def _json_location(item: dict[str, object]) -> str:
    return format_location(
        item.get("file") or item.get("path"),
        item.get("line"),
        item.get("column"),
    )


def _absorb_json_item(
    item: dict[str, object],
    accum: _JsonAccum,
    max_snippet_lines: int,
) -> None:
    severity = str(item.get("severity") or item.get("type") or "").lower()
    code = str(item.get("code") or "")
    message = str(item.get("message") or "")[:400]
    if severity == "note":
        accum.notes += 1
        if accum.findings:
            prev = accum.findings[-1]
            accum.findings[-1] = Finding(
                id=prev.id,
                kind=prev.kind,
                message=prev.message,
                location=prev.location,
                detail=tuple(clamp([*prev.detail, message], max_snippet_lines)),
            )
        return
    if severity == "warning":
        accum.warnings += 1
        accum.bump_code(code)
        return
    if severity != "error":
        return
    location = _json_location(item)
    accum.bump_code(code)
    finding_id = f"{location} {code}".strip() if code else location
    accum.findings.append(
        Finding(
            id=finding_id,
            kind="type_error",
            message=message,
            location=location,
        )
    )


class MypyParser:
    name = "mypy"

    def parse(self, ctx: ParseContext) -> ParseResult:
        fmt = str(ctx.options.get("format") or "text").lower()
        if fmt == "json":
            return self._parse_json(ctx)
        return self._parse_text(ctx)

    def _parse_text(self, ctx: ParseContext) -> ParseResult:
        findings: list[Finding] = []
        warning_count = 0
        note_count = 0
        by_code: Counter[str] = Counter()
        current: Finding | None = None
        current_detail: list[str] = []

        def flush() -> None:
            nonlocal current, current_detail
            if current is not None:
                findings.append(
                    Finding(
                        id=current.id,
                        kind=current.kind,
                        message=current.message,
                        location=current.location,
                        detail=tuple(clamp(current_detail, ctx.max_snippet_lines)),
                    )
                )
            current = None
            current_detail = []

        for raw in ctx.log_text.splitlines():
            match = _LINE_RE.match(raw.rstrip())
            if match is None:
                continue
            severity = match.group("severity")
            location = format_location(
                match.group("path"),
                match.group("line"),
                match.group("col"),
            )
            message = match.group("message").strip()[:400]
            code = match.group("code") or ""

            if severity == "note":
                note_count += 1
                if current is not None:
                    current_detail.append(raw.rstrip())
                continue
            if severity == "warning":
                flush()
                warning_count += 1
                if code:
                    by_code[code] += 1
                continue

            flush()
            if code:
                by_code[code] += 1
            finding_id = f"{location} {code}".strip() if code else location
            current = Finding(
                id=finding_id,
                kind="type_error",
                message=message,
                location=location,
            )
            current_detail = []
        flush()
        return self._finish(ctx, findings, warning_count, note_count, by_code)

    def _parse_json(self, ctx: ParseContext) -> ParseResult:
        accum = _JsonAccum()
        for item in _iter_json_objects(ctx.log_text):
            _absorb_json_item(item, accum, ctx.max_snippet_lines)
        result = self._finish(
            ctx,
            accum.findings,
            accum.warnings,
            accum.notes,
            Counter(accum.codes),
        )
        if (
            result.parser_ok
            and ctx.rc != 0
            and not accum.findings
            and not _CLEAN_RE.search(ctx.log_text)
            and not _FOUND_RE.search(ctx.log_text)
        ):
            result.parser_ok = False
            result.notes.append(
                "mypy exited nonzero but no JSON errors were parsed and no "
                "clean marker is present; inspect log_tail"
            )
        return result

    def _finish(
        self,
        ctx: ParseContext,
        findings: list[Finding],
        warning_count: int,
        note_count: int,
        by_code: Counter[str],
    ) -> ParseResult:
        result = ParseResult(
            findings=findings,
            summary={
                "error_count": len(findings),
                "warning_count": warning_count,
                "note_count": note_count,
                "errors_by_code": top_counts(dict(by_code), ctx.top),
            },
        )
        self._verify(ctx, result, len(findings))
        return result

    @staticmethod
    def _verify(ctx: ParseContext, result: ParseResult, error_count: int) -> None:
        declared_match = _FOUND_RE.search(ctx.log_text)
        if declared_match is not None:
            declared = int(declared_match.group("n"))
            if declared != error_count:
                result.parser_ok = False
                result.notes.append(
                    f"mypy declares {declared} error(s) but {error_count} "
                    "were parsed; the output format may have changed -- "
                    "refusing to trust this parse"
                )
                return
        if ctx.rc != 0 and not error_count and not _CLEAN_RE.search(ctx.log_text):
            result.parser_ok = False
            result.notes.append(
                "mypy exited nonzero but no errors were parsed and no "
                "'Success: no issues found' marker is present; inspect log_tail"
            )
