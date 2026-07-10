# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Parser fact-extraction tests, including the loud-failure guards."""

from pathlib import Path
from typing import Any

import pytest

from ckdn.parsers.bandit_json import BanditJsonParser
from ckdn.parsers.base import ParseContext
from ckdn.parsers.coverage_xml import CoverageXmlParser
from ckdn.parsers.mypy import MypyParser
from ckdn.parsers.pip_audit_json import PipAuditJsonParser
from ckdn.parsers.pylint_json import PylintJsonParser
from ckdn.parsers.pyright_json import PyrightJsonParser
from ckdn.parsers.pytest_junit import PytestJUnitParser
from ckdn.parsers.reformat_text import ReformatTextParser
from ckdn.parsers.ruff_json import RuffJsonParser
from ckdn.parsers.sarif import SarifParser
from ckdn.parsers.ty_text import TyTextParser

JUNIT_ONE_FAILURE = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="1" skipped="1" tests="3">
    <testcase classname="tests.test_math" name="test_add"
              file="tests/test_math.py" line="4"/>
    <testcase classname="tests.test_math" name="test_div"
              file="tests/test_math.py" line="9">
      <failure message="assert 1 == 2">def test_div():
&gt;       assert 1 == 2
E       assert 1 == 2</failure>
    </testcase>
    <testcase classname="tests.test_math" name="test_skip"><skipped/></testcase>
  </testsuite>
</testsuites>
"""

COVERAGE_XML = """\
<?xml version="1.0" ?>
<coverage line-rate="0.8" branch-rate="0.5">
  <packages><package name="pkg">
    <classes>
      <class name="mod.py" filename="src/pkg/mod.py">
        <lines>
          <line number="1" hits="1"/>
          <line number="2" hits="0"/>
          <line number="3" hits="1" branch="true" condition-coverage="50% (1/2)"/>
        </lines>
      </class>
    </classes>
  </package></packages>
</coverage>
"""

TY_TWO_ERRORS = """\
error[invalid-assignment]: Object of type `str` is not assignable to `int`
 --> src/pkg/mod.py:10:5
   |
10 |     x: int = "a"
   |
error[unresolved-import]: Cannot resolve import `missing`
 --> src/pkg/other.py:1:8
Found 2 diagnostics
"""

TY_COUNT_MISMATCH = """\
some future format the regexes do not understand
Found 3 diagnostics
"""

RUFF_JSON = """\
[{"code": "F401", "filename": "src/pkg/mod.py",
  "location": {"row": 1, "column": 8},
  "message": "`os` imported but unused",
  "fix": {"applicability": "safe"}}]
"""


def ctx(run_dir: Path, rc: int, log: str = "", **options: Any) -> ParseContext:
    return ParseContext(
        run_dir=run_dir,
        log_text=log,
        rc=rc,
        options=options,
        top=20,
        max_snippet_lines=12,
    )


def test_pytest_extracts_failure_from_junit(tmp_path: Path) -> None:
    (tmp_path / "junit.xml").write_text(JUNIT_ONE_FAILURE, encoding="utf-8")
    result = PytestJUnitParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.id == "tests.test_math::test_div"
    assert finding.kind == "test_failure"
    assert finding.location == "tests/test_math.py:9"
    assert any("assert 1 == 2" in line for line in finding.detail)
    assert result.summary["counts"] == {
        "tests": 3,
        "failures": 1,
        "errors": 0,
        "skipped": 1,
    }


def test_pytest_missing_junit_flips_parser_ok(tmp_path: Path) -> None:
    result = PytestJUnitParser().parse(ctx(tmp_path, rc=2))
    assert result.parser_ok is False


def test_coverage_gate_fails_below_threshold(tmp_path: Path) -> None:
    (tmp_path / "coverage.xml").write_text(COVERAGE_XML, encoding="utf-8")
    result = CoverageXmlParser().parse(ctx(tmp_path, rc=0, fail_under=95.0))
    assert result.gate_failures, "80% < 95% must trip the gate"
    assert result.summary["overall"]["line_percent"] == 80.0
    top = result.summary["top_uncovered_files"][0]
    assert top["file"] == "src/pkg/mod.py"
    assert top["missing_lines_preview"] == [2]
    assert top["branch_gaps_preview"] == ["3: 50% (1/2)"]


def test_coverage_gate_passes_above_threshold(tmp_path: Path) -> None:
    (tmp_path / "coverage.xml").write_text(COVERAGE_XML, encoding="utf-8")
    result = CoverageXmlParser().parse(ctx(tmp_path, rc=0, fail_under=75.0))
    assert result.gate_failures == []


def test_coverage_merges_junit_findings(tmp_path: Path) -> None:
    (tmp_path / "coverage.xml").write_text(COVERAGE_XML, encoding="utf-8")
    (tmp_path / "junit.xml").write_text(JUNIT_ONE_FAILURE, encoding="utf-8")
    result = CoverageXmlParser().parse(ctx(tmp_path, rc=1, fail_under=75.0))
    assert len(result.findings) == 1
    assert result.findings[0].kind == "test_failure"


def test_ty_parses_block_format(tmp_path: Path) -> None:
    result = TyTextParser().parse(ctx(tmp_path, rc=1, log=TY_TWO_ERRORS))
    assert result.parser_ok
    assert len(result.findings) == 2
    assert result.findings[0].location == "src/pkg/mod.py:10:5"
    assert result.summary["errors_by_code"] == {
        "invalid-assignment": 1,
        "unresolved-import": 1,
    }


def test_ty_declared_count_mismatch_flips_parser_ok(tmp_path: Path) -> None:
    """The crown-jewel guard: format drift must fail loudly, never 'clean'."""
    result = TyTextParser().parse(ctx(tmp_path, rc=1, log=TY_COUNT_MISMATCH))
    assert result.parser_ok is False


def test_ty_nonzero_without_diagnostics_flips_parser_ok(tmp_path: Path) -> None:
    result = TyTextParser().parse(ctx(tmp_path, rc=101, log="panic: oops"))
    assert result.parser_ok is False


def test_ruff_reads_json_report(tmp_path: Path) -> None:
    (tmp_path / "ruff.json").write_text(RUFF_JSON, encoding="utf-8")
    result = RuffJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok
    assert len(result.findings) == 1
    assert result.findings[0].location == "src/pkg/mod.py:1:8"
    assert result.summary["fixable_count"] == 1


def test_ruff_missing_report_flips_parser_ok(tmp_path: Path) -> None:
    result = RuffJsonParser().parse(ctx(tmp_path, rc=2))
    assert result.parser_ok is False


# --- mypy -------------------------------------------------------------------

MYPY_TEXT = """\
src/pkg/mod.py:10: error: Incompatible types [assignment]
src/pkg/mod.py:10: note: Expected "int"
src/pkg/other.py:1:5: error: Cannot find module [import-not-found]
Found 2 errors in 2 files (checked 3 source files)
"""

MYPY_JSON = (
    '{"file":"src/pkg/mod.py","line":10,"column":5,'
    '"severity":"error","message":"Incompatible types","code":"assignment"}\n'
    '{"file":"src/pkg/mod.py","line":10,"column":5,'
    '"severity":"note","message":"Expected int","code":""}\n'
)


def test_mypy_parses_text_with_notes(tmp_path: Path) -> None:
    result = MypyParser().parse(ctx(tmp_path, rc=1, log=MYPY_TEXT))
    assert result.parser_ok
    assert len(result.findings) == 2
    assert result.findings[0].location == "src/pkg/mod.py:10"
    assert any("Expected" in line for line in result.findings[0].detail)
    assert result.findings[1].location == "src/pkg/other.py:1:5"
    assert result.summary["error_count"] == 2
    assert result.summary["note_count"] == 1


def test_mypy_count_mismatch_flips_parser_ok(tmp_path: Path) -> None:
    log = "src/a.py:1: error: x [x]\nFound 3 errors in 1 file\n"
    result = MypyParser().parse(ctx(tmp_path, rc=1, log=log))
    assert result.parser_ok is False


def test_mypy_nonzero_without_errors_flips_parser_ok(tmp_path: Path) -> None:
    result = MypyParser().parse(ctx(tmp_path, rc=2, log="usage: mypy"))
    assert result.parser_ok is False


def test_mypy_json_notes_and_warnings(tmp_path: Path) -> None:
    log = "\n".join(
        [
            '{"severity":"error","file":"a.py","line":1,"column":1,'
            '"code":"attr-defined","message":"bad"}',
            '{"severity":"note","message":"revealed type is Any"}',
            '{"severity":"warning","code":"unused","message":"warn"}',
            '{"severity":"info","message":"skip"}',
            "not-json",
            "{bad",
        ]
    )
    result = MypyParser().parse(ctx(tmp_path, rc=1, log=log, format="json"))
    assert result.findings
    assert result.summary["note_count"] == 1
    assert result.summary["warning_count"] == 1


def test_mypy_text_warning_and_note(tmp_path: Path) -> None:
    log = (
        "a.py:1:1: error: bad  [attr-defined]\n"
        "a.py:1:1: note: follow-up\n"
        "b.py:2:1: warning: soft  [unused]\n"
    )
    result = MypyParser().parse(ctx(tmp_path, rc=1, log=log))
    assert len(result.findings) == 1
    assert result.summary["warning_count"] == 1
    assert result.summary["note_count"] == 1


def test_mypy_json_nonzero_empty(tmp_path: Path) -> None:
    result = MypyParser().parse(ctx(tmp_path, rc=1, log="noise\n", format="json"))
    assert result.parser_ok is False


def test_sarif_location_and_message_variants(tmp_path: Path) -> None:
    payload = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "x", "version": "1"}},
                "results": [
                    {
                        "ruleId": "R1",
                        "level": "error",
                        "message": "plain",
                        "locations": [{"not": "phys"}],
                    },
                    {
                        "ruleId": "R2",
                        "message": {"text": "obj"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "a.py"},
                                    "region": {"startLine": 3},
                                }
                            }
                        ],
                    },
                    {
                        "ruleId": "R3",
                        "level": "error",
                        "message": {"text": "uri-only"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "b.py"},
                                }
                            }
                        ],
                    },
                ],
            }
        ],
    }
    import json as _json

    (tmp_path / "report.sarif").write_text(_json.dumps(payload), encoding="utf-8")
    result = SarifParser().parse(ctx(tmp_path, rc=1, fail_levels="error"))
    assert result.parser_ok
    assert len(result.findings) >= 2


def test_pylint_skips_non_dict_messages_and_score_notes(tmp_path: Path) -> None:
    payload = """\
{
  "messages": ["skip-me", {
      "type": "error",
      "messageId": "E0001",
      "symbol": "syntax-error",
      "message": "bad",
      "path": "a.py",
      "line": 1,
      "column": 0
  }],
  "statistics": {"score": null, "messageTypeCount": {"error": "x", "warning": 0}}
}
"""
    (tmp_path / "pylint.json").write_text(payload, encoding="utf-8")
    result = PylintJsonParser().parse(ctx(tmp_path, rc=1))
    assert len(result.findings) == 1
    result2 = PylintJsonParser().parse(ctx(tmp_path, rc=1, score_fail_under=9.0))
    assert any("score" in n.lower() for n in result2.notes)
    assert not result2.gate_failures

    result = MypyParser().parse(ctx(tmp_path, rc=1, log=MYPY_JSON, format="json"))
    assert result.parser_ok
    assert len(result.findings) == 1
    assert result.summary["note_count"] == 1
    assert any("Expected int" in line for line in result.findings[0].detail)


# --- pyright ----------------------------------------------------------------

PYRIGHT_JSON = """\
npm warn something
{
  "version": "1.1.0",
  "generalDiagnostics": [
    {
      "file": "src/pkg/mod.py",
      "severity": "error",
      "message": "Type error",
      "rule": "reportGeneralTypeIssues",
      "range": {
        "start": {"line": 9, "character": 4},
        "end": {"line": 9, "character": 5}
      }
    },
    {
      "file": "src/pkg/mod.py",
      "severity": "warning",
      "message": "Unused",
      "rule": "reportUnused",
      "range": {
        "start": {"line": 1, "character": 0},
        "end": {"line": 1, "character": 1}
      }
    }
  ],
  "summary": {"errorCount": 1, "warningCount": 1, "informationCount": 0}
}
node done
"""


def test_pyright_extracts_json_from_noisy_log(tmp_path: Path) -> None:
    result = PyrightJsonParser().parse(ctx(tmp_path, rc=1, log=PYRIGHT_JSON))
    assert result.parser_ok
    assert len(result.findings) == 1
    assert result.findings[0].kind == "type_error"
    assert result.findings[0].location == "src/pkg/mod.py:10:5"
    assert result.summary["warning_count"] == 1


def test_pyright_count_mismatch_flips_parser_ok(tmp_path: Path) -> None:
    bad = PYRIGHT_JSON.replace('"errorCount": 1', '"errorCount": 9')
    result = PyrightJsonParser().parse(ctx(tmp_path, rc=1, log=bad))
    assert result.parser_ok is False


def test_pyright_missing_json_flips_parser_ok(tmp_path: Path) -> None:
    result = PyrightJsonParser().parse(ctx(tmp_path, rc=1, log="no json here"))
    assert result.parser_ok is False


# --- reformat ---------------------------------------------------------------

BLACK_LOG = """\
would reformat src/a.py
would reformat src/b.py
Oh no! \U0001f4a5 \U0001f608 \U0001f4a5
2 files would be reformatted, 1 file would be left unchanged.
"""

RUFF_FORMAT_LOG = """\
Would reformat: src/a.py
Would reformat: src/b.py
2 files would be reformatted
"""


def test_reformat_black_dialect(tmp_path: Path) -> None:
    result = ReformatTextParser().parse(ctx(tmp_path, rc=1, log=BLACK_LOG))
    assert result.parser_ok
    assert len(result.findings) == 2
    assert result.findings[0].kind == "format_violation"


def test_reformat_ruff_dialect(tmp_path: Path) -> None:
    result = ReformatTextParser().parse(ctx(tmp_path, rc=1, log=RUFF_FORMAT_LOG))
    assert result.parser_ok
    assert result.summary["file_count"] == 2


def test_reformat_count_mismatch_flips_parser_ok(tmp_path: Path) -> None:
    log = "would reformat src/a.py\n3 files would be reformatted\n"
    result = ReformatTextParser().parse(ctx(tmp_path, rc=1, log=log))
    assert result.parser_ok is False


def test_reformat_nonzero_empty_flips_parser_ok(tmp_path: Path) -> None:
    result = ReformatTextParser().parse(ctx(tmp_path, rc=123, log="internal error"))
    assert result.parser_ok is False


# --- pip_audit --------------------------------------------------------------

PIP_AUDIT_JSON = """\
{
  "dependencies": [
    {
      "name": "requests",
      "version": "2.28.0",
      "vulns": [
        {
          "id": "GHSA-xxxx",
          "description": "Bad thing",
          "fix_versions": ["2.31.0"],
          "aliases": ["CVE-2023-1"]
        }
      ]
    }
  ],
  "skipped": [{"name": "local-pkg", "skip_reason": "not on index"}]
}
"""


def test_pip_audit_reads_report(tmp_path: Path) -> None:
    (tmp_path / "pip-audit.json").write_text(PIP_AUDIT_JSON, encoding="utf-8")
    result = PipAuditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok
    assert len(result.findings) == 1
    assert result.findings[0].kind == "vulnerability"
    assert result.summary["skipped_packages"] == 1
    assert any("skipped" in n for n in result.notes)


def test_pip_audit_missing_report_flips_parser_ok(tmp_path: Path) -> None:
    result = PipAuditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False


def test_pip_audit_invalid_json_flips_parser_ok(tmp_path: Path) -> None:
    (tmp_path / "pip-audit.json").write_text("{not json", encoding="utf-8")
    result = PipAuditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False


def test_pip_audit_wrong_shape_flips_parser_ok(tmp_path: Path) -> None:
    (tmp_path / "pip-audit.json").write_text("[]", encoding="utf-8")
    result = PipAuditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False


# --- bandit -----------------------------------------------------------------

BANDIT_JSON = """\
{
  "results": [
    {
      "filename": "src/a.py",
      "line_number": 3,
      "test_id": "B101",
      "issue_text": "Use of assert",
      "issue_severity": "LOW",
      "issue_confidence": "HIGH",
      "issue_cwe": {"id": 703}
    }
  ],
  "metrics": {
    "_totals": {
      "SEVERITY.HIGH": 0,
      "SEVERITY.MEDIUM": 0,
      "SEVERITY.LOW": 1,
      "SEVERITY.UNDEFINED": 0
    }
  }
}
"""


def test_bandit_reads_report(tmp_path: Path) -> None:
    (tmp_path / "bandit.json").write_text(BANDIT_JSON, encoding="utf-8")
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok
    assert len(result.findings) == 1
    assert result.findings[0].kind == "security_issue"
    assert "CWE-703" in result.findings[0].detail


def test_bandit_metrics_mismatch_flips_parser_ok(tmp_path: Path) -> None:
    bad = BANDIT_JSON.replace('"SEVERITY.LOW": 1', '"SEVERITY.LOW": 5')
    (tmp_path / "bandit.json").write_text(bad, encoding="utf-8")
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False


def test_bandit_missing_report_flips_parser_ok(tmp_path: Path) -> None:
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False


def test_bandit_per_file_metrics_mismatch(tmp_path: Path) -> None:
    payload = """\
{
  "results": [],
  "metrics": {
    "src/a.py": {
      "SEVERITY.HIGH": 0,
      "SEVERITY.MEDIUM": 0,
      "SEVERITY.LOW": 2,
      "SEVERITY.UNDEFINED": 0
    }
  }
}
"""
    (tmp_path / "bandit.json").write_text(payload, encoding="utf-8")
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False
    assert any("metrics imply" in n for n in result.notes)


def test_bandit_empty_metrics_ok(tmp_path: Path) -> None:
    payload = """\
{"results": [], "metrics": {}}
"""
    (tmp_path / "bandit.json").write_text(payload, encoding="utf-8")
    result = BanditJsonParser().parse(ctx(tmp_path, rc=0))
    assert result.parser_ok


# --- pylint -----------------------------------------------------------------

PYLINT_JSON = """\
{
  "messages": [
    {
      "type": "convention",
      "messageId": "C0114",
      "symbol": "missing-module-docstring",
      "message": "Missing module docstring",
      "path": "src/a.py",
      "line": 1,
      "column": 0
    },
    {
      "type": "error",
      "messageId": "E0602",
      "symbol": "undefined-variable",
      "message": "Undefined variable 'x'",
      "path": "src/a.py",
      "line": 4,
      "column": 1
    }
  ],
  "statistics": {
    "messageTypeCount": {"convention": 1, "error": 1, "warning": 0, "refactor": 0},
    "score": 8.5
  }
}
"""


def test_pylint_reads_json2(tmp_path: Path) -> None:
    (tmp_path / "pylint.json").write_text(PYLINT_JSON, encoding="utf-8")
    result = PylintJsonParser().parse(ctx(tmp_path, rc=3, score_fail_under=9.0))
    assert result.parser_ok
    assert len(result.findings) == 2
    assert result.gate_failures
    assert "score" in result.gate_failures[0]


def test_pylint_score_gate_passes(tmp_path: Path) -> None:
    (tmp_path / "pylint.json").write_text(PYLINT_JSON, encoding="utf-8")
    result = PylintJsonParser().parse(ctx(tmp_path, rc=3, score_fail_under=8.0))
    assert result.gate_failures == []


def test_pylint_count_mismatch_flips_parser_ok(tmp_path: Path) -> None:
    bad = PYLINT_JSON.replace('"convention": 1', '"convention": 9')
    (tmp_path / "pylint.json").write_text(bad, encoding="utf-8")
    result = PylintJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False


def test_pylint_missing_report_flips_parser_ok(tmp_path: Path) -> None:
    result = PylintJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False


# --- sarif ------------------------------------------------------------------

SARIF_JSON = """\
{
  "version": "2.1.0",
  "runs": [
    {
      "tool": {"driver": {"name": "semgrep", "version": "1.0"}},
      "results": [
        {
          "ruleId": "python.lang.security.audit",
          "level": "error",
          "message": {"text": "Bad pattern"},
          "locations": [{
            "physicalLocation": {
              "artifactLocation": {"uri": "src/a.py"},
              "region": {"startLine": 10}
            }
          }]
        },
        {
          "ruleId": "python.style",
          "level": "warning",
          "message": {"text": "Style"},
          "locations": [{
            "physicalLocation": {
              "artifactLocation": {"uri": "src/b.py"},
              "region": {"startLine": 2}
            }
          }]
        },
        {
          "ruleId": "python.missing-level",
          "message": {"text": "No level"},
          "locations": [{
            "physicalLocation": {
              "artifactLocation": {"uri": "src/c.py"},
              "region": {"startLine": 1}
            }
          }]
        }
      ]
    }
  ]
}
"""


def test_sarif_default_fail_levels_errors_only(tmp_path: Path) -> None:
    (tmp_path / "report.sarif").write_text(SARIF_JSON, encoding="utf-8")
    result = SarifParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok
    assert len(result.findings) == 1
    assert result.findings[0].kind == "sarif_error"
    assert result.summary["by_level"]["warning"] == 2  # warning + missing level


def test_sarif_custom_fail_levels(tmp_path: Path) -> None:
    (tmp_path / "report.sarif").write_text(SARIF_JSON, encoding="utf-8")
    result = SarifParser().parse(ctx(tmp_path, rc=1, fail_levels=["error", "warning"]))
    assert len(result.findings) == 3


def test_sarif_missing_report_flips_parser_ok(tmp_path: Path) -> None:
    result = SarifParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False


def test_sarif_invalid_shape_flips_parser_ok(tmp_path: Path) -> None:
    (tmp_path / "report.sarif").write_text('{"version": "2.1.0"}', encoding="utf-8")
    result = SarifParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False


def test_coverage_missing_xml(tmp_path: Path) -> None:
    result = CoverageXmlParser().parse(ctx(tmp_path, rc=0, fail_under=50.0))
    assert result.parser_ok is False


def test_coverage_invalid_xml(tmp_path: Path) -> None:
    (tmp_path / "coverage.xml").write_text("<not-xml", encoding="utf-8")
    result = CoverageXmlParser().parse(ctx(tmp_path, rc=0, fail_under=50.0))
    assert result.parser_ok is False


def test_coverage_skips_gate_without_fail_under(tmp_path: Path) -> None:
    (tmp_path / "coverage.xml").write_text(COVERAGE_XML, encoding="utf-8")
    result = CoverageXmlParser().parse(ctx(tmp_path, rc=0))
    assert result.parser_ok
    assert any("fail_under" in n for n in result.notes)


def test_coverage_bad_numeric_attrs(tmp_path: Path) -> None:
    xml = COVERAGE_XML.replace('line-rate="0.8"', 'line-rate="nope"')
    (tmp_path / "coverage.xml").write_text(xml, encoding="utf-8")
    result = CoverageXmlParser().parse(ctx(tmp_path, rc=0, fail_under=1.0))
    assert result.parser_ok


def test_ruff_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "ruff.json").write_text("{", encoding="utf-8")
    result = RuffJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False


def test_ruff_wrong_shape(tmp_path: Path) -> None:
    (tmp_path / "ruff.json").write_text('{"x": 1}', encoding="utf-8")
    result = RuffJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False


def test_pytest_invalid_junit(tmp_path: Path) -> None:
    (tmp_path / "junit.xml").write_text("<bad", encoding="utf-8")
    result = PytestJUnitParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False


def test_pylint_score_gate_fails(tmp_path: Path) -> None:
    (tmp_path / "pylint.json").write_text(PYLINT_JSON, encoding="utf-8")
    result = PylintJsonParser().parse(ctx(tmp_path, rc=3, score_fail_under=10.0))
    assert result.gate_failures


def test_clamp_and_format_location() -> None:
    from ckdn.parsers.base import clamp, format_location, top_counts

    assert clamp(["a", "b"], 0) == []
    assert clamp(["a", "b", "c"], 2)[-1].startswith("...")
    assert format_location("a.py") == "a.py"
    assert format_location(None, 1) == "?:1"
    assert top_counts({"a": 1, "b": 3}, 0) == {"b": 3, "a": 1}


def test_available_parsers_lists_builtins() -> None:
    from ckdn.parsers import available_parsers

    names = available_parsers()
    assert "generic" in names and "ruff" in names


def test_module_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy

    monkeypatch.setattr("ckdn.cli.main", lambda: 0)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("ckdn.__main__", run_name="__main__")
    assert exc.value.code == 0
