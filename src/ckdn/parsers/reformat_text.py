# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Format-check parser covering black ``--check`` and ruff ``format --check``.

Dialect is selected automatically from distinctive line prefixes. Do not pass
``--diff`` — diffs are unbounded.

Expected command shapes:

    uv run black --check src tests
    uv run ruff format --check .
"""

from __future__ import annotations

import re

from ckdn.parsers.base import Finding, ParseContext, ParseResult

_BLACK_FILE_RE = re.compile(r"^would reformat\s+(?P<path>.+)$")
_RUFF_FILE_RE = re.compile(r"^Would reformat:\s+(?P<path>.+)$")
_SUMMARY_RE = re.compile(
    r"(?P<n>\d+)\s+files?\s+would\s+be\s+reformatted", re.IGNORECASE
)
_BLACK_CLEAN_RE = re.compile(r"All done!")
_RUFF_CLEAN_RE = re.compile(r"(?P<n>\d+)\s+files?\s+already\s+formatted", re.IGNORECASE)


class ReformatTextParser:
    name = "reformat"

    def parse(self, ctx: ParseContext) -> ParseResult:
        paths: list[str] = []
        for raw in ctx.log_text.splitlines():
            line = raw.rstrip()
            black = _BLACK_FILE_RE.match(line)
            if black is not None:
                paths.append(black.group("path").strip())
                continue
            ruff = _RUFF_FILE_RE.match(line)
            if ruff is not None:
                paths.append(ruff.group("path").strip())

        # Deduplicate while preserving order.
        seen: set[str] = set()
        unique: list[str] = []
        for path in paths:
            if path not in seen:
                seen.add(path)
                unique.append(path)

        findings = [
            Finding(
                id=path,
                kind="format_violation",
                message=f"would reformat {path}",
                location=path,
            )
            for path in unique
        ]
        result = ParseResult(
            findings=findings,
            summary={"file_count": len(findings)},
        )
        self._verify(ctx, result, len(findings))
        return result

    @staticmethod
    def _verify(ctx: ParseContext, result: ParseResult, file_count: int) -> None:
        summary = _SUMMARY_RE.search(ctx.log_text)
        if summary is not None:
            declared = int(summary.group("n"))
            if declared != file_count:
                result.parser_ok = False
                result.notes.append(
                    f"format tool declares {declared} file(s) would be "
                    f"reformatted but {file_count} were parsed; refusing "
                    "to trust this parse"
                )
                return
        clean = _BLACK_CLEAN_RE.search(ctx.log_text) or _RUFF_CLEAN_RE.search(
            ctx.log_text
        )
        if ctx.rc != 0 and file_count == 0 and not clean:
            result.parser_ok = False
            result.notes.append(
                "format check exited nonzero but no reformattable files were "
                "parsed and no clean marker is present; inspect log_tail"
            )
