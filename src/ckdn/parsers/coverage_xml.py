# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Coverage parser backed by Cobertura-style coverage XML.

Design decisions:

* The coverage gate is evaluated against the XML numbers and the
  ``fail_under`` value from ckdn's own config -- never against a regex
  over pytest-cov's terminal message, whose wording changes across versions.
  This also means the gate holds even when pytest itself exits 0 (e.g. no
  ``fail-under`` configured on the pytest side).
* If a JUnit report is present in the run directory, failed tests are
  extracted as findings too: a broken test run must not hide behind a
  pleasant coverage number.

Expected command shape:

    uv run pytest -q --junitxml {run_dir}/junit.xml \
        --cov=<pkg> --cov-report=term-missing \
        --cov-report=xml:{run_dir}/coverage.xml

Options: ``fail_under`` (float, recommended), ``coverage_xml``, ``junit``,
``missing_lines_preview`` (int, default 40).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import Any, TypeVar

from ckdn.parsers.base import ParseContext, ParseResult
from ckdn.parsers.pytest_junit import parse_junit

_T = TypeVar("_T")

#: Cap on branch-gap detail lines reported per file.
_BRANCH_GAP_PREVIEW = 10


def _num(node: ET.Element, attr: str, cast: Callable[[str], _T], default: _T) -> _T:
    raw = node.get(attr)
    if raw is None:
        return default
    try:
        return cast(raw)
    except ValueError:
        return default


def _class_gap_entry(cls: ET.Element, preview: int) -> dict[str, Any] | None:
    """Return uncovered-file stats, or None when the class has no gaps."""
    covered = total = 0
    missing: list[int] = []
    branch_gaps: list[str] = []
    for line in cls.findall("./lines/line"):
        total += 1
        hits = _num(line, "hits", int, 0)
        number = _num(line, "number", int, 0)
        if hits > 0:
            covered += 1
        elif number:
            missing.append(number)
        if line.get("branch") == "true":
            cond = line.get("condition-coverage") or ""
            if cond and not cond.startswith("100%"):
                branch_gaps.append(f"{number}: {cond}")
    if not missing and not branch_gaps:
        return None
    rate = covered / total if total else 1.0
    return {
        "file": cls.get("filename") or "",
        "line_rate": round(rate, 4),
        "missing_count": len(missing),
        "missing_lines_preview": missing[:preview],
        "missing_lines_truncated": max(0, len(missing) - preview),
        "branch_gaps_preview": branch_gaps[:_BRANCH_GAP_PREVIEW],
        "branch_gaps_truncated": max(0, len(branch_gaps) - _BRANCH_GAP_PREVIEW),
    }


def _collect_files(root: ET.Element, preview: int) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cls in root.findall(".//class"):
        filename = cls.get("filename") or ""
        if not filename or filename in seen:
            continue
        seen.add(filename)
        entry = _class_gap_entry(cls, preview)
        if entry is not None:
            files.append(entry)
    files.sort(key=lambda f: (f["line_rate"], -f["missing_count"], f["file"]))
    return files


class CoverageXmlParser:
    name = "coverage"

    def parse(self, ctx: ParseContext) -> ParseResult:
        xml_path = ctx.artifact("coverage_xml", "coverage.xml")
        if not xml_path.exists():
            return ParseResult(
                parser_ok=False,
                notes=[
                    f"coverage XML not found: {xml_path}. The check command "
                    "must include `--cov-report=xml:{run_dir}/coverage.xml`."
                ],
            )
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError as exc:
            return ParseResult(
                parser_ok=False,
                notes=[f"coverage XML is not valid XML: {exc}"],
            )

        line_rate = _num(root, "line-rate", float, 0.0)
        branch_rate = _num(root, "branch-rate", float, 0.0)
        line_percent = round(line_rate * 100, 2)
        preview = int(ctx.options.get("missing_lines_preview", 40))
        files = _collect_files(root, preview)

        result = ParseResult(
            summary={
                "overall": {
                    "line_percent": line_percent,
                    "branch_percent": round(branch_rate * 100, 2),
                },
                "uncovered_files_total": len(files),
                "top_uncovered_files": files[: ctx.top],
            }
        )

        fail_under = ctx.options.get("fail_under")
        if fail_under is None:
            result.notes.append(
                "no `fail_under` configured for this check; coverage gate skipped"
            )
        elif line_percent < float(fail_under):
            result.gate_failures.append(
                f"line coverage {line_percent}% is below "
                f"fail_under={float(fail_under)}%"
            )
        result.summary["fail_under"] = fail_under

        # Merge test failures from junit, if the report exists.
        junit_path = ctx.artifact("junit", "junit.xml")
        if junit_path.exists():
            junit_result = parse_junit(
                junit_path.read_text(encoding="utf-8", errors="replace"),
                ctx.max_snippet_lines,
            )
            result.findings.extend(junit_result.findings)
            result.summary["counts"] = junit_result.summary.get("counts")
            if not junit_result.parser_ok:
                result.parser_ok = False
                result.notes.extend(junit_result.notes)
        else:
            result.notes.append(
                "no junit report in run dir; test failures were not "
                "cross-checked (add `--junitxml {run_dir}/junit.xml`)"
            )

        return result
