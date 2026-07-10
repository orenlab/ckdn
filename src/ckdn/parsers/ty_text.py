# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""ty (Astral type checker) parser over text output.

ty is pre-1.0 and does not yet guarantee a stable machine-readable format,
so this is the one built-in parser that reads terminal text. It compensates
with two loud-failure guards instead of trusting its regexes:

1. ty prints a trailing ``Found N diagnostics`` summary. If the parsed
   diagnostic count disagrees with N, ``parser_ok`` flips off.
2. A nonzero exit code with zero parsed diagnostics and no explicit
   "All checks passed!" marker also flips ``parser_ok`` off.

Either way the result is never silently green. Supported layouts: the
default block format (``error[code]: message`` + ``--> path:line:col``) and
the concise one-line format.

Warnings are counted in the summary but are NOT emitted as findings:
ty exits 0 when only warnings are present, and warning-findings with rc == 0
would trip the reconciler's mismatch guard by design.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ckdn.parsers.base import Finding, ParseContext, ParseResult, clamp

_HEADER_RE = re.compile(
    r"^(?P<level>error|warning)\[(?P<code>[^\]]+)\]:?\s*(?P<message>.*)$"
)
_LOCATION_RE = re.compile(r"^\s*-->\s*(?P<path>.+?):(?P<line>\d+):(?P<col>\d+)")
_CONCISE_RE = re.compile(
    r"^(?P<path>[^\s:][^:]*):(?P<line>\d+):(?P<col>\d+):\s*"
    r"(?P<level>error|warning)\[(?P<code>[^\]]+)\]\s*(?P<message>.*)$"
)
_FOUND_RE = re.compile(r"Found\s+(?P<n>\d+)\s+diagnostic")
_CLEAN_RE = re.compile(r"All checks passed")


@dataclass
class _Diagnostic:
    """One parsed ty error diagnostic (warnings are counted, not collected)."""

    code: str
    message: str
    location: str | None = None
    detail: list[str] = field(default_factory=list)


class _Scanner:
    """Line-oriented scanner for ty's block and concise output layouts."""

    def __init__(self, max_snippet_lines: int) -> None:
        self._max = max_snippet_lines
        self.diagnostics: list[_Diagnostic] = []
        self.warning_count = 0
        self._current: _Diagnostic | None = None
        self._context: list[str] = []

    def feed(self, line: str) -> None:
        if self._feed_concise(line):
            return
        if self._feed_header(line):
            return
        self._feed_context(line)

    def finish(self) -> None:
        self._flush()

    def _flush(self) -> None:
        if self._current is not None:
            self._current.detail = clamp(
                [ln for ln in self._context if ln.strip()], self._max
            )
            self.diagnostics.append(self._current)
        self._current = None
        self._context = []

    def _feed_concise(self, line: str) -> bool:
        match = _CONCISE_RE.match(line)
        if match is None:
            return False
        self._flush()
        if match.group("level") == "warning":
            self.warning_count += 1
        else:
            self.diagnostics.append(
                _Diagnostic(
                    code=match.group("code"),
                    message=match.group("message").strip(),
                    location=(
                        f"{match.group('path')}:"
                        f"{match.group('line')}:{match.group('col')}"
                    ),
                )
            )
        return True

    def _feed_header(self, line: str) -> bool:
        match = _HEADER_RE.match(line)
        if match is None:
            return False
        self._flush()
        if match.group("level") == "warning":
            self.warning_count += 1
        else:
            self._current = _Diagnostic(
                code=match.group("code"),
                message=match.group("message").strip(),
            )
        return True

    def _feed_context(self, line: str) -> None:
        if self._current is None:
            return
        loc = _LOCATION_RE.match(line)
        if loc is not None and self._current.location is None:
            self._current.location = (
                f"{loc.group('path')}:{loc.group('line')}:{loc.group('col')}"
            )
        elif line.strip():
            self._context.append(line)


class TyTextParser:
    name = "ty"

    def parse(self, ctx: ParseContext) -> ParseResult:
        scanner = _Scanner(ctx.max_snippet_lines)
        for raw in ctx.log_text.splitlines():
            scanner.feed(raw.rstrip())
        scanner.finish()

        diagnostics = scanner.diagnostics
        findings = [
            Finding(
                id=f"{diag.location or '?'} {diag.code}",
                kind="type_error",
                message=diag.message[:400],
                location=diag.location,
                detail=tuple(diag.detail),
            )
            for diag in diagnostics
        ]

        by_code: dict[str, int] = {}
        for diag in diagnostics:
            by_code[diag.code] = by_code.get(diag.code, 0) + 1

        result = ParseResult(
            findings=findings,
            summary={
                "error_count": len(findings),
                "warning_count": scanner.warning_count,
                "errors_by_code": dict(sorted(by_code.items())),
            },
        )
        self._verify(ctx, result, findings, scanner.warning_count)
        return result

    @staticmethod
    def _verify(
        ctx: ParseContext,
        result: ParseResult,
        findings: list[Finding],
        warning_count: int,
    ) -> None:
        """Apply the two loud-failure guards to a provisional result."""
        declared_match = _FOUND_RE.search(ctx.log_text)
        declared = int(declared_match.group("n")) if declared_match else None
        parsed_total = len(findings) + warning_count
        if declared is not None and declared != parsed_total:
            result.parser_ok = False
            result.notes.append(
                f"ty declares {declared} diagnostic(s) but {parsed_total} "
                "were parsed; the output format may have changed -- "
                "refusing to trust this parse"
            )
        elif ctx.rc != 0 and not findings and not _CLEAN_RE.search(ctx.log_text):
            result.parser_ok = False
            result.notes.append(
                "ty exited nonzero but no diagnostics were parsed and no "
                "'All checks passed' marker is present; inspect log_tail"
            )
