# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""pytest parser backed by the JUnit XML report.

Design decision: parse the machine-readable report, not the terminal text.
Terminal output format shifts across pytest versions and plugins; JUnit XML
is a stable, documented artifact. The check command MUST write it:

    uv run pytest -q --junitxml {run_dir}/junit.xml

Self-consistency guard: the number of extracted findings is cross-checked
against the counts declared in the ``<testsuite>`` attributes. Any
disagreement flips ``parser_ok`` off -- ckdn fails loudly rather than
reporting a partially-parsed result as truth.
"""

from __future__ import annotations

import contextlib
import xml.etree.ElementTree as ET

from ckdn.parsers.base import Finding, ParseContext, ParseResult, clamp

_COUNT_KEYS = ("tests", "failures", "errors", "skipped")


def _snippet(text: str, limit: int) -> tuple[str, ...]:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    marked = [ln for ln in lines if ln.lstrip().startswith("E ")]
    chosen = marked if marked else lines[-limit:]
    return tuple(clamp(chosen, limit))


def parse_junit(junit_text: str, max_snippet_lines: int) -> ParseResult:
    """Parse JUnit XML content into a ParseResult (shared with coverage)."""
    try:
        root = ET.fromstring(junit_text)
    except ET.ParseError as exc:
        return ParseResult(
            parser_ok=False, notes=[f"junit report is not valid XML: {exc}"]
        )

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    counts = dict.fromkeys(_COUNT_KEYS, 0)
    for suite in suites:
        for key in _COUNT_KEYS:
            with contextlib.suppress(ValueError):
                counts[key] += int(suite.get(key, 0) or 0)

    findings: list[Finding] = []
    for case in root.iter("testcase"):
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        nodeid = f"{classname}::{name}" if classname else name
        file_attr = case.get("file")
        line_attr = case.get("line")
        location = None
        if file_attr:
            location = f"{file_attr}:{line_attr}" if line_attr else file_attr
        for node in case:
            if node.tag not in ("failure", "error"):
                continue
            findings.append(
                Finding(
                    id=nodeid,
                    kind="test_error" if node.tag == "error" else "test_failure",
                    message=(node.get("message") or "").strip()[:400],
                    location=location,
                    detail=_snippet(node.text or "", max_snippet_lines),
                )
            )

    result = ParseResult(
        findings=findings,
        summary={"counts": counts},
    )
    declared = counts["failures"] + counts["errors"]
    if declared != len(findings):
        result.parser_ok = False
        result.notes.append(
            f"junit declares {declared} failure(s)/error(s) but "
            f"{len(findings)} were extracted; refusing to trust this parse"
        )
    return result


class PytestJUnitParser:
    name = "pytest"

    def parse(self, ctx: ParseContext) -> ParseResult:
        junit_path = ctx.artifact("junit", "junit.xml")
        if not junit_path.exists():
            return ParseResult(
                parser_ok=False,
                notes=[
                    f"junit report not found: {junit_path}. The check command "
                    "must include `--junitxml {run_dir}/junit.xml`. A missing "
                    "report with rc != 0 usually means pytest crashed before "
                    "or during collection -- see log_tail."
                ],
            )
        return parse_junit(
            junit_path.read_text(encoding="utf-8", errors="replace"),
            ctx.max_snippet_lines,
        )
