# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Format-check parser covering black ``--check`` and ruff ``format --check``.

Dialect is selected automatically from distinctive line shapes. Do not pass
``--diff`` — diffs are unbounded.

Expected command shapes:

    uv run black --check src tests
    uv run ruff format --check .

ruff changed this output in 0.16. Before it: ``Would reformat: <path>``. Since,
one ``unformatted`` diagnostic per file in ruff's ``output-format``, which a
project can set in its own config — so the ``full`` default (a header, then
`` --> <path>:<line>:<col>``), ``concise`` and ``grouped`` are all read.
Other diagnostics in the same output (``invalid-syntax``) are not findings.
"""

from __future__ import annotations

import re

from ckdn.parsers.base import Finding, ParseContext, ParseResult

_BLACK_FILE_RE = re.compile(r"^would reformat\s+(?P<path>.+)$")
# ruff < 0.16
_RUFF_FILE_RE = re.compile(r"^Would reformat:\s+(?P<path>.+)$")
# ruff >= 0.16, `concise`: `a.py:1:2: unformatted: File would be reformatted`
_RUFF_CONCISE_RE = re.compile(r"^(?P<path>.+?):\d+:\d+:\s+unformatted:\s")
# ruff >= 0.16, `full` (default): the header, then ` --> a.py:1:2` right under it
_RUFF_FULL_HEADER_RE = re.compile(r"^unformatted:\s")
_RUFF_FULL_ARROW_RE = re.compile(r"^\s*-->\s+(?P<path>.+?):\d+:\d+$")
# ruff >= 0.16, `grouped`: `a.py:`, then indented `  1:2 unformatted: ...`
_RUFF_GROUPED_FILE_RE = re.compile(r"^(?P<path>\S.*):$")
_RUFF_GROUPED_ENTRY_RE = re.compile(r"^\s+\d+:\d+\s+unformatted:\s")
_ONE_LINE_RES = (_BLACK_FILE_RE, _RUFF_FILE_RE, _RUFF_CONCISE_RE)
_SUMMARY_RE = re.compile(
    r"(?P<n>\d+)\s+files?\s+would\s+be\s+reformatted", re.IGNORECASE
)
_BLACK_CLEAN_RE = re.compile(r"All done!")
_RUFF_CLEAN_RE = re.compile(r"(?P<n>\d+)\s+files?\s+already\s+formatted", re.IGNORECASE)


def _one_line_path(line: str) -> str | None:
    for regex in _ONE_LINE_RES:
        match = regex.match(line)
        if match is not None:
            return match.group("path").strip()
    return None


def _reformat_paths(lines: list[str]) -> list[str]:
    """Every file the output says would be reformatted, in order."""
    paths: list[str] = []
    grouped_file: str | None = None
    for index, line in enumerate(lines):
        path = _one_line_path(line)
        if path is None and _RUFF_FULL_HEADER_RE.match(line):
            # Only the line directly under the header: a diff line below it
            # can contain anything, `-->` included.
            following = lines[index + 1] if index + 1 < len(lines) else ""
            arrow = _RUFF_FULL_ARROW_RE.match(following)
            path = arrow.group("path") if arrow is not None else None
        if path is not None:
            paths.append(path)
        if _RUFF_GROUPED_ENTRY_RE.match(line):
            if grouped_file is not None:
                paths.append(grouped_file)
            continue
        header = _RUFF_GROUPED_FILE_RE.match(line)
        grouped_file = header.group("path") if header is not None else None
    return paths


class ReformatTextParser:
    name = "reformat"

    def parse(self, ctx: ParseContext) -> ParseResult:
        paths = _reformat_paths([raw.rstrip() for raw in ctx.log_text.splitlines()])

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
