# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Parser fact-extraction tests, including the loud-failure guards."""

from pathlib import Path
from typing import Any

import pytest

from ckdn.parsers.bandit_json import BanditJsonParser
from ckdn.parsers.base import ArtifactPathError, ParseContext, artifact_path
from ckdn.parsers.coverage_xml import CoverageXmlParser
from ckdn.parsers.mypy import MypyParser
from ckdn.parsers.pip_audit_json import PipAuditJsonParser
from ckdn.parsers.pre_commit_text import PreCommitTextParser
from ckdn.parsers.pylint_json import PylintJsonParser
from ckdn.parsers.pyright_json import PyrightJsonParser
from ckdn.parsers.pytest_junit import PytestJUnitParser
from ckdn.parsers.reformat_text import ReformatTextParser
from ckdn.parsers.ruff_json import RuffJsonParser
from ckdn.parsers.sarif import SarifParser
from ckdn.parsers.ty_text import TyTextParser
from ckdn.reconcile import reconcile

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


def test_artifact_path_absolute_not_redoubled(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-abc"
    run_dir.mkdir()
    abs_junit = (run_dir / "junit.xml").resolve()
    abs_junit.write_text(JUNIT_ONE_FAILURE, encoding="utf-8")
    assert artifact_path(run_dir, str(abs_junit)) == abs_junit
    assert artifact_path(run_dir, "{run_dir}/junit.xml") == abs_junit
    substituted = f"{run_dir}/junit.xml"
    assert artifact_path(run_dir, substituted) == abs_junit


def test_artifact_path_rejects_escape(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with pytest.raises(ArtifactPathError, match="escapes run directory"):
        artifact_path(run_dir, "/etc/passwd")
    with pytest.raises(ArtifactPathError, match="escapes run directory"):
        artifact_path(run_dir, "../../../etc/passwd")
    with pytest.raises(ArtifactPathError, match="escapes run directory"):
        artifact_path(run_dir, "{run_dir}/../../../etc/passwd")


def test_artifact_path_rejects_symlink_escape(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outside = tmp_path / "outside.xml"
    outside.write_text("<testsuites/>", encoding="utf-8")
    link = run_dir / "junit.xml"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    with pytest.raises(ArtifactPathError, match="escapes run directory"):
        artifact_path(run_dir, "junit.xml")


def test_pytest_rejects_artifact_escape_via_options(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with pytest.raises(ArtifactPathError):
        PytestJUnitParser().parse(
            ctx(run_dir, rc=0, junit="/etc/passwd"),
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


# --- pre_commit -------------------------------------------------------------

PRE_COMMIT_FAIL = """\
Fail Hook................................................................Failed
- hook id: fail-hook
- exit code: 1

line1
line2

Pass Hook................................................................Passed
"""

PRE_COMMIT_MODIFIED = """\
fix eof..................................................................Failed
- hook id: fix-eof
- files were modified by this hook
"""

PRE_COMMIT_SKIPPED = """\
check for broken symlinks............................(no files to check)Skipped
Ruff (lint)..............................................................Passed
"""

PRE_COMMIT_VERBOSE_PASS = """\
Ruff (lint)..............................................................Passed
- hook id: ruff-check
- duration: 0.03s

All checks passed!
"""


def test_pre_commit_parses_failed_hook_output(tmp_path: Path) -> None:
    result = PreCommitTextParser().parse(ctx(tmp_path, rc=1, log=PRE_COMMIT_FAIL))
    assert result.parser_ok
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.id == "fail-hook"
    assert finding.kind == "hook_failure"
    assert finding.message == "Fail Hook failed (exit code 1)"
    assert "line1" in finding.detail
    assert result.summary == {
        "hooks_total": 2,
        "passed": 1,
        "failed": 1,
        "skipped": 0,
        "failed_hooks": ["fail-hook"],
    }


def test_pre_commit_parses_modified_files_failure(tmp_path: Path) -> None:
    result = PreCommitTextParser().parse(ctx(tmp_path, rc=1, log=PRE_COMMIT_MODIFIED))
    assert result.parser_ok
    assert len(result.findings) == 1
    assert result.findings[0].id == "fix-eof"
    assert result.findings[0].message == "fix eof modified files"


def test_pre_commit_counts_skipped_hooks(tmp_path: Path) -> None:
    result = PreCommitTextParser().parse(ctx(tmp_path, rc=0, log=PRE_COMMIT_SKIPPED))
    assert result.parser_ok
    assert result.findings == []
    assert result.summary["hooks_total"] == 2
    assert result.summary["skipped"] == 1
    assert result.summary["passed"] == 1


def test_pre_commit_verbose_pass_has_no_findings(tmp_path: Path) -> None:
    result = PreCommitTextParser().parse(
        ctx(tmp_path, rc=0, log=PRE_COMMIT_VERBOSE_PASS)
    )
    assert result.parser_ok
    assert result.findings == []
    assert result.summary["passed"] == 1


def test_pre_commit_nonzero_without_failures_flips_parser_ok(tmp_path: Path) -> None:
    result = PreCommitTextParser().parse(
        ctx(tmp_path, rc=1, log=PRE_COMMIT_VERBOSE_PASS)
    )
    assert result.parser_ok is False


def test_pre_commit_unparsed_output_flips_parser_ok(tmp_path: Path) -> None:
    result = PreCommitTextParser().parse(
        ctx(tmp_path, rc=1, log="pre-commit internal error\n")
    )
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


# --- bandit: tool-side --severity-level / --confidence-level filtering -------
#
# Verified against bandit 1.9.4: `metrics` (both `_totals` and the per-file
# maps) counts every issue the scan found, while `results` is filtered by
# `--severity-level` / `--confidence-level`. Summing all severity buckets and
# comparing to len(findings) therefore fires on every filtered run.


def _bandit_issue(
    severity: str,
    confidence: str = "HIGH",
    *,
    test_id: str = "B324",
    filename: str = "src/a.py",
    line_number: int = 6,
) -> dict[str, Any]:
    """One `results` entry, shaped like the real bandit JSON report."""
    return {
        "code": "5 def h(data):\n6     return hashlib.md5(data).hexdigest()\n",
        "col_offset": 11,
        "end_col_offset": 28,
        "filename": filename,
        "issue_confidence": confidence,
        "issue_cwe": {
            "id": 327,
            "link": "https://cwe.mitre.org/data/definitions/327.html",
        },
        "issue_severity": severity,
        "issue_text": "Use of weak MD5 hash for security.",
        "line_number": line_number,
        "line_range": [line_number],
        "more_info": "https://bandit.readthedocs.io/en/1.9.4/plugins/b324.html",
        "test_id": test_id,
        "test_name": "hashlib",
    }


def _bandit_buckets(**counts: int) -> dict[str, int]:
    """A metrics table with bandit's full bucket set plus the `loc` extras."""
    table: dict[str, int] = {}
    for axis in ("SEVERITY", "CONFIDENCE"):
        for rank in ("UNDEFINED", "LOW", "MEDIUM", "HIGH"):
            table[f"{axis}.{rank}"] = counts.get(f"{axis}_{rank}".lower(), 0)
    table.update({"loc": 13, "nosec": 0, "skipped_tests": 0})
    return table


def _skip_note(declared_total: int) -> str:
    """The exact note the parser records when it abstains from the check."""
    return (
        f"bandit metrics count {declared_total} issue(s) that `results` does "
        "not list; tool-side --severity-level/--confidence-level filtering is "
        "invisible in metrics, so the metrics cross-check was skipped"
    )


def test_bandit_severity_level_filtering_keeps_parser_ok(tmp_path: Path) -> None:
    """`--severity-level high`: 3 HIGH survive, 3 LOW are filtered tool-side.

    Transcribed from a real `uvx bandit -r . -f json --severity-level high`
    run (bandit 1.9.4): `results` holds the 3 HIGH issues, while both the
    per-file maps and `_totals` still declare all 6.
    """
    payload = {
        "errors": [],
        "generated_at": "2026-08-17T00:00:00Z",
        "results": [
            _bandit_issue("HIGH", filename="src/high.py"),
            _bandit_issue("HIGH", filename="src/high.py"),
            _bandit_issue("HIGH", filename="src/low.py"),
        ],
        "metrics": {
            "src/high.py": _bandit_buckets(severity_high=2, confidence_high=2),
            "src/low.py": _bandit_buckets(
                severity_high=1, severity_low=3, confidence_high=4
            ),
            "_totals": _bandit_buckets(
                severity_high=3, severity_low=3, confidence_high=6
            ),
        },
    }
    _write_json(tmp_path / "bandit.json", payload)
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is True
    assert len(result.findings) == 3
    assert result.notes == []


def test_bandit_floor_is_the_lowest_rank_present_in_results(tmp_path: Path) -> None:
    """The floor is the lowest rank `results` shows, not the first or highest.

    HIGH comes first in `results`, so a floor taken from the leading entry (or
    from the maximum) would count only the HIGH bucket and report a loss.
    """
    payload = {
        "results": [_bandit_issue("HIGH"), _bandit_issue("LOW")],
        "metrics": {
            "_totals": _bandit_buckets(
                severity_high=1, severity_low=1, confidence_high=2
            )
        },
    }
    _write_json(tmp_path / "bandit.json", payload)
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is True
    assert result.notes == []


def test_bandit_totals_alias_is_accepted(tmp_path: Path) -> None:
    """`totals` is the alias the parser accepts alongside bandit's `_totals`.

    With the aggregate table recognised, the per-file map is not summed on
    top of it; treating `totals` as a per-file entry would double-count.
    """
    payload = {
        "results": [_bandit_issue("LOW")],
        "metrics": {
            "src/a.py": _bandit_buckets(severity_low=1, confidence_high=1),
            "totals": _bandit_buckets(severity_low=1, confidence_high=1),
        },
    }
    _write_json(tmp_path / "bandit.json", payload)
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is True


def test_bandit_per_file_severity_filtering_keeps_parser_ok(tmp_path: Path) -> None:
    """The per-file metrics branch has the same flaw and the same fix."""
    payload = {
        "results": [_bandit_issue("HIGH", filename="src/high.py")],
        "metrics": {
            "src/high.py": _bandit_buckets(severity_high=1, confidence_high=1),
            "src/low.py": _bandit_buckets(severity_low=3, confidence_high=3),
        },
    }
    _write_json(tmp_path / "bandit.json", payload)
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is True


def test_bandit_confidence_level_filtering_keeps_parser_ok(tmp_path: Path) -> None:
    """`--confidence-level high` filters the orthogonal axis; same fix."""
    payload = {
        "results": [_bandit_issue("LOW", "HIGH") for _ in range(2)],
        "metrics": {
            "_totals": _bandit_buckets(
                severity_low=5, confidence_high=2, confidence_low=3
            )
        },
    }
    _write_json(tmp_path / "bandit.json", payload)
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is True


def test_bandit_both_axes_filtered_skips_cross_check(tmp_path: Path) -> None:
    """`--severity-level medium --confidence-level high` (i.e. `-ll -ii`).

    Transcribed from a real bandit 1.9.4 run over a file with one HIGH/HIGH
    (md5), one MEDIUM/MEDIUM (hardcoded /tmp path) and one LOW/HIGH (assert)
    issue: only the HIGH/HIGH one survives both cuts. Per-axis marginals
    cannot reconstruct that joint count, so the cross-check abstains.
    """
    payload = {
        "results": [_bandit_issue("HIGH", "HIGH", filename="src/m.py")],
        "metrics": {
            "src/m.py": _bandit_buckets(
                severity_low=1,
                severity_medium=1,
                severity_high=1,
                confidence_medium=1,
                confidence_high=2,
            ),
            "_totals": _bandit_buckets(
                severity_low=1,
                severity_medium=1,
                severity_high=1,
                confidence_medium=1,
                confidence_high=2,
            ),
        },
    }
    _write_json(tmp_path / "bandit.json", payload)
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is True
    assert len(result.findings) == 1
    assert result.notes == [_skip_note(3)]


BANDIT_EVERYTHING_FILTERED = {
    "errors": [],
    "generated_at": "2026-08-17T00:00:00Z",
    "results": [],
    "metrics": {
        "src/asserts.py": {
            "CONFIDENCE.HIGH": 5,
            "CONFIDENCE.LOW": 0,
            "CONFIDENCE.MEDIUM": 0,
            "CONFIDENCE.UNDEFINED": 0,
            "SEVERITY.HIGH": 0,
            "SEVERITY.LOW": 5,
            "SEVERITY.MEDIUM": 0,
            "SEVERITY.UNDEFINED": 0,
            "loc": 7,
            "nosec": 0,
            "skipped_tests": 0,
        },
        "_totals": {
            "CONFIDENCE.HIGH": 5,
            "CONFIDENCE.LOW": 0,
            "CONFIDENCE.MEDIUM": 0,
            "CONFIDENCE.UNDEFINED": 0,
            "SEVERITY.HIGH": 0,
            "SEVERITY.LOW": 5,
            "SEVERITY.MEDIUM": 0,
            "SEVERITY.UNDEFINED": 0,
            "loc": 7,
            "nosec": 0,
            "skipped_tests": 0,
        },
    },
}
"""Verbatim `uvx bandit -r . -f json --severity-level medium` output (bandit
1.9.4) over a file holding five `assert` statements: rc 0, zero results, and
metrics that still count all five."""


def test_bandit_everything_filtered_out_keeps_parser_ok(tmp_path: Path) -> None:
    """5 LOW issues, `--severity-level medium`: bandit exits 0 with no results.

    The parse is lossless by construction (zero in, zero out), so the metrics
    totals must not manufacture a permanent parse_mismatch.
    """
    _write_json(tmp_path / "bandit.json", BANDIT_EVERYTHING_FILTERED)
    result = BanditJsonParser().parse(ctx(tmp_path, rc=0))
    assert result.parser_ok is True
    assert result.findings == []
    assert result.notes == [_skip_note(5)]


def test_bandit_empty_results_with_failing_rc_still_trips(tmp_path: Path) -> None:
    """rc != 0 means bandit found issues at or above its level; `results` must
    not be empty. Filtering cannot explain this, so the guard still fires."""
    _write_json(tmp_path / "bandit.json", BANDIT_EVERYTHING_FILTERED)
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False
    assert any("metrics imply 5 issue(s) but 0 were parsed" in n for n in result.notes)


def test_bandit_lost_finding_above_the_floor_still_trips(tmp_path: Path) -> None:
    """Regression guard: 3 HIGH declared at or above the observed floor but
    only 2 parsed is a lost finding, not filtering."""
    payload = {
        "results": [_bandit_issue("HIGH") for _ in range(2)],
        "metrics": {
            "_totals": _bandit_buckets(
                severity_high=3, severity_low=3, confidence_high=6
            )
        },
    }
    _write_json(tmp_path / "bandit.json", payload)
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False
    assert any("metrics imply 3 issue(s) but 2 were parsed" in n for n in result.notes)


def test_bandit_more_parsed_than_declared_still_trips(tmp_path: Path) -> None:
    """Filtering only ever removes results, so parsing more than the metrics
    declare is a contradiction in either direction."""
    payload = {
        "results": [_bandit_issue("HIGH") for _ in range(4)],
        "metrics": {"_totals": _bandit_buckets(severity_high=3, confidence_high=3)},
    }
    _write_json(tmp_path / "bandit.json", payload)
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False
    assert any("metrics imply 3 issue(s) but 4 were parsed" in n for n in result.notes)


def test_bandit_unrecognised_severity_values_skip_that_axis(tmp_path: Path) -> None:
    """A `results` entry whose severity is not one of bandit's four ranks gives
    no floor for that axis; the confidence axis still carries the check."""
    payload = {
        "results": [_bandit_issue("BOGUS", "HIGH")],
        "metrics": {"_totals": _bandit_buckets(severity_low=4, confidence_high=1)},
    }
    _write_json(tmp_path / "bandit.json", payload)
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is True
    # The confidence axis was usable, so the check ran rather than abstained.
    assert result.notes == []


def test_bandit_unreadable_results_entries_do_not_hide_the_floor(
    tmp_path: Path,
) -> None:
    """Robustness: entries the floor scan has to skip must not end the scan.

    A non-dict entry and an unrecognised severity both precede the one entry
    that carries the real severity floor. Abandoning the scan at either would
    lose the severity axis entirely.
    """
    payload = {
        "results": [
            "not a dict",
            _bandit_issue("BOGUS", "HIGH"),
            _bandit_issue("HIGH", "HIGH"),
        ],
        "metrics": {
            "_totals": _bandit_buckets(
                severity_low=3, severity_high=2, confidence_high=5
            )
        },
    }
    _write_json(tmp_path / "bandit.json", payload)
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert len(result.findings) == 2
    assert result.parser_ok is True
    assert result.notes == []


@pytest.mark.parametrize(
    ("declared", "parser_ok", "note"),
    [(4, False, "metrics imply 4 issue(s) but 1 were parsed"), (1, True, None)],
)
def test_bandit_no_usable_axis_compares_the_declared_total(
    tmp_path: Path, declared: int, parser_ok: bool, note: str | None
) -> None:
    """No recognisable rank on either axis: no discount is justified.

    Being unable to infer how much filtering removed is not a licence to accept
    any gap, so the declared total is compared as it stands -- 4 declared
    against 1 parsed is a lost parse, while counts that agree are trusted.
    """
    payload = {
        "results": [_bandit_issue("BOGUS", "BOGUS")],
        "metrics": {
            "_totals": _bandit_buckets(severity_low=declared, confidence_high=declared)
        },
    }
    _write_json(tmp_path / "bandit.json", payload)
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is parser_ok
    if note is None:
        assert result.notes == []
    else:
        assert any(note in n for n in result.notes)


def test_bandit_all_zero_metrics_never_trip(tmp_path: Path) -> None:
    """A clean scan declares zeros everywhere; the guard stays silent."""
    payload = {
        "results": [],
        "metrics": {"_totals": _bandit_buckets()},
    }
    _write_json(tmp_path / "bandit.json", payload)
    result = BanditJsonParser().parse(ctx(tmp_path, rc=0))
    assert result.parser_ok is True
    assert result.notes == []


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


# Captured verbatim from `uvx pylint clean.py --enable=useless-suppression
# --output-format=json2` (pylint 4.0.7), on a module whose only remark is a
# needless `# pylint: disable=invalid-name`. That run exits 0: pylint's rc is
# a bitmask over F/E/W/R/C (1/2/4/8/16) and the informational class has no
# bit. `absolutePath` is dropped because it is machine-specific.
PYLINT_INFO_ONLY_JSON = """\
{
    "messages": [
        {
            "type": "info",
            "symbol": "useless-suppression",
            "message": "Useless suppression of 'invalid-name'",
            "messageId": "I0021",
            "confidence": "UNDEFINED",
            "module": "clean",
            "obj": "",
            "line": 6,
            "column": 0,
            "endLine": null,
            "endColumn": null,
            "path": "clean.py"
        }
    ],
    "statistics": {
        "messageTypeCount": {
            "fatal": 0,
            "error": 0,
            "warning": 0,
            "refactor": 0,
            "convention": 0,
            "info": 1
        },
        "modulesLinted": 3,
        "score": 10.0
    }
}
"""


def test_pylint_informational_messages_are_not_findings(tmp_path: Path) -> None:
    """An info-only pylint run exits 0; it must not manufacture findings.

    A finding alongside ``rc == 0`` reconciles to ``parse_mismatch``, which
    the user cannot fix from ckdn's side.
    """
    (tmp_path / "pylint.json").write_text(PYLINT_INFO_ONLY_JSON, encoding="utf-8")
    result = PylintJsonParser().parse(ctx(tmp_path, rc=0))
    assert result.parser_ok is True
    assert result.findings == []
    assert result.summary["info_count"] == 1
    assert result.summary["message_count"] == 0


def test_pylint_info_only_run_reconciles_to_pass(tmp_path: Path) -> None:
    """End-to-end: rc 0 plus informational output is a pass, not a mismatch."""
    (tmp_path / "pylint.json").write_text(PYLINT_INFO_ONLY_JSON, encoding="utf-8")
    result = PylintJsonParser().parse(ctx(tmp_path, rc=0))
    status, _reason, _tail = reconcile(0, result)
    assert status == "pass"


def test_pylint_info_still_counted_for_the_statistics_crosscheck(
    tmp_path: Path,
) -> None:
    """Dropping info findings must not blind ``_verify_counts``.

    ``statistics.messageTypeCount`` declares an ``info`` bucket, so the
    parser has to keep counting info messages even though they never become
    findings -- otherwise the guard trades one false ``parse_mismatch`` for
    another.
    """
    lying = PYLINT_INFO_ONLY_JSON.replace('"info": 1', '"info": 5')
    (tmp_path / "pylint.json").write_text(lying, encoding="utf-8")
    result = PylintJsonParser().parse(ctx(tmp_path, rc=0))
    assert result.parser_ok is False
    assert any("messageTypeCount[info]=5" in n for n in result.notes)


# Captured verbatim from `uvx pylint mixed.py --enable=useless-suppression
# --output-format=json2` (pylint 4.0.7); that run exits 16 -- the convention
# bit alone, with the info message contributing nothing.
PYLINT_MIXED_JSON = """\
{
    "messages": [
        {
            "type": "convention",
            "symbol": "missing-module-docstring",
            "message": "Missing module docstring",
            "messageId": "C0114",
            "confidence": "HIGH",
            "module": "mixed",
            "obj": "",
            "line": 1,
            "column": 0,
            "path": "mixed.py"
        },
        {
            "type": "info",
            "symbol": "useless-suppression",
            "message": "Useless suppression of 'invalid-name'",
            "messageId": "I0021",
            "confidence": "UNDEFINED",
            "module": "mixed",
            "obj": "",
            "line": 2,
            "column": 0,
            "path": "mixed.py"
        }
    ],
    "statistics": {
        "messageTypeCount": {
            "fatal": 0,
            "error": 0,
            "warning": 0,
            "refactor": 0,
            "convention": 1,
            "info": 1
        },
        "modulesLinted": 3,
        "score": 0
    }
}
"""


def test_pylint_mixed_report_keeps_only_the_scoring_message(tmp_path: Path) -> None:
    """Info is filtered out of findings while its neighbours survive intact."""
    (tmp_path / "pylint.json").write_text(PYLINT_MIXED_JSON, encoding="utf-8")
    result = PylintJsonParser().parse(ctx(tmp_path, rc=16))
    assert result.parser_ok is True
    assert [f.id for f in result.findings] == ["C0114 mixed.py:1:0"]
    assert result.summary["info_count"] == 1
    assert result.summary["by_type"] == {"convention": 1, "info": 1}


def test_pylint_info_message_does_not_truncate_the_scan(tmp_path: Path) -> None:
    """An info message skips itself, not the rest of the list.

    pylint emits messages in source order, so an informational remark can sit
    ahead of a real one. Abandoning the loop there would silently swallow
    every finding behind it.
    """
    payload = {
        "messages": [
            {
                "type": "info",
                "messageId": "I0021",
                "symbol": "useless-suppression",
                "message": "Useless suppression of 'invalid-name'",
                "path": "a.py",
                "line": 1,
                "column": 0,
            },
            {
                "type": "error",
                "messageId": "E0602",
                "symbol": "undefined-variable",
                "message": "Undefined variable 'x'",
                "path": "a.py",
                "line": 9,
                "column": 4,
            },
        ],
        "statistics": {
            "messageTypeCount": {"info": 1, "error": 1},
            "score": 5.0,
        },
    }
    _write_json(tmp_path / "pylint.json", payload)
    result = PylintJsonParser().parse(ctx(tmp_path, rc=2))
    assert result.parser_ok is True
    assert [f.id for f in result.findings] == ["E0602 a.py:9:4"]
    assert result.summary["info_count"] == 1


@pytest.mark.parametrize(
    "msg_type",
    ["fatal", "error", "warning", "refactor", "convention"],
)
def test_pylint_scoring_classes_all_become_findings(
    tmp_path: Path, msg_type: str
) -> None:
    """Every class that owns an rc bit must still produce a finding."""
    payload = {
        "messages": [
            {
                "type": msg_type,
                "messageId": "X0001",
                "symbol": "some-symbol",
                "message": "boom",
                "path": "a.py",
                "line": 3,
                "column": 2,
            }
        ],
        "statistics": {"messageTypeCount": {msg_type: 1, "info": 0}, "score": 1.0},
    }
    _write_json(tmp_path / "pylint.json", payload)
    result = PylintJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is True
    assert [f.id for f in result.findings] == ["X0001 a.py:3:2"]
    assert result.summary["info_count"] == 0


def test_pylint_info_lookalike_types_are_not_filtered(tmp_path: Path) -> None:
    """Only the exact ``info`` class is exempt -- no prefix or substring match."""
    payload = {
        "messages": [
            {
                "type": "information",
                "messageId": "X0002",
                "symbol": "s",
                "message": "m",
                "path": "a.py",
                "line": 1,
                "column": 0,
            },
            {
                "type": "informational",
                "messageId": "X0003",
                "symbol": "s",
                "message": "m",
                "path": "a.py",
                "line": 2,
                "column": 0,
            },
        ],
        "statistics": {"score": 1.0},
    }
    _write_json(tmp_path / "pylint.json", payload)
    result = PylintJsonParser().parse(ctx(tmp_path, rc=1))
    assert len(result.findings) == 2
    assert result.summary["info_count"] == 0


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
    assert "generic" in names and "pre_commit" in names and "ruff" in names


def test_module_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy

    monkeypatch.setattr("ckdn.cli.main", lambda: 0)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("ckdn.__main__", run_name="__main__")
    assert exc.value.code == 0


# --- malformed reports: the defensive branches -------------------------------
#
# Real tools emit these shapes on their bad days (a plugin crash mid-report, a
# truncated write, a schema change). Each one must degrade to a note or a
# skipped entry, never to a traceback and never to a silent zero-finding pass.


def _write_json(path: Path, payload: Any) -> None:
    import json as _json

    path.write_text(_json.dumps(payload), encoding="utf-8")


def test_sarif_tolerates_malformed_runs_and_locations(tmp_path: Path) -> None:
    payload = {
        "version": "2.1.0",
        "runs": [
            "not a run object",
            {"tool": "not a table", "results": []},
            {"tool": {"driver": "not a table"}, "results": []},
            {
                "tool": {"driver": {"name": "x"}},
                "results": [
                    {"ruleId": "R1", "level": "error", "locations": "not a list"},
                    {"ruleId": "R2", "level": "error", "locations": []},
                    {"ruleId": "R3", "level": "error", "locations": ["not a dict"]},
                    {
                        "ruleId": "R4",
                        "level": "error",
                        "locations": [{"physicalLocation": "not a table"}],
                    },
                    {
                        "ruleId": "R5",
                        "level": "error",
                        "locations": [{"physicalLocation": {"artifactLocation": 7}}],
                    },
                ],
            },
        ],
    }
    _write_json(tmp_path / "report.sarif", payload)
    result = SarifParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok
    # Every result is still counted; none of them resolves to a location.
    assert len(result.findings) == 5
    assert all(f.location is None for f in result.findings)
    # Only the one well-formed driver contributes tool metadata.
    assert result.summary["tools"] == [{"name": "x", "version": ""}]


def test_sarif_runs_not_an_array_flips_parser_ok(tmp_path: Path) -> None:
    _write_json(tmp_path / "report.sarif", {"version": "2.1.0", "runs": {}})
    result = SarifParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False
    assert any("not an array" in n for n in result.notes)


def test_sarif_fail_levels_accepts_a_bare_string_or_junk(tmp_path: Path) -> None:
    (tmp_path / "report.sarif").write_text(SARIF_JSON, encoding="utf-8")
    as_string = SarifParser().parse(ctx(tmp_path, rc=1, fail_levels="warning"))
    assert {f.kind for f in as_string.findings} == {"sarif_warning"}
    # A number is not a level list; fall back to the documented default.
    junk = SarifParser().parse(ctx(tmp_path, rc=1, fail_levels=7))
    assert {f.kind for f in junk.findings} == {"sarif_error"}


def test_pyright_tolerates_malformed_diagnostics(tmp_path: Path) -> None:
    payload = {
        "generalDiagnostics": [
            "not a diagnostic",
            {"severity": "information", "message": "fyi"},
            {"file": "a.py", "severity": "error", "range": "not a table"},
            {"file": "b.py", "severity": "error", "range": {"start": "not a table"}},
            {"file": "c.py", "severity": "error", "range": {"start": {"line": "x"}}},
        ],
        "summary": {"errorCount": 3, "warningCount": 0},
    }
    import json as _json

    result = PyrightJsonParser().parse(ctx(tmp_path, rc=1, log=_json.dumps(payload)))
    assert result.parser_ok
    assert [f.location for f in result.findings] == ["a.py", "b.py", "c.py"]


def test_pyright_warning_count_mismatch_flips_parser_ok(tmp_path: Path) -> None:
    bad = PYRIGHT_JSON.replace('"warningCount": 1', '"warningCount": 4')
    result = PyrightJsonParser().parse(ctx(tmp_path, rc=1, log=bad))
    assert result.parser_ok is False
    assert any("warningCount=4" in n for n in result.notes)


TY_CONCISE = """\
src/pkg/mod.py:10:5: error[invalid-assignment] Object of type `str`
src/pkg/other.py:1:8: warning[unused-ignore] Unused ignore
Found 2 diagnostics
"""


def test_ty_parses_concise_format(tmp_path: Path) -> None:
    result = TyTextParser().parse(ctx(tmp_path, rc=1, log=TY_CONCISE))
    assert result.parser_ok
    assert len(result.findings) == 1
    assert result.findings[0].location == "src/pkg/mod.py:10:5"
    assert result.findings[0].id == "src/pkg/mod.py:10:5 invalid-assignment"
    # Warnings are counted, never collected: ty exits 0 on warnings alone.
    assert result.summary["warning_count"] == 1


def test_ty_block_warning_is_counted_not_collected(tmp_path: Path) -> None:
    log = (
        "warning[unused-ignore]: Unused ignore\n --> src/a.py:1:1\nFound 1 diagnostic\n"
    )
    result = TyTextParser().parse(ctx(tmp_path, rc=0, log=log))
    assert result.parser_ok
    assert result.findings == []
    assert result.summary["warning_count"] == 1


def test_ruff_skips_non_dict_entries(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "ruff.json",
        ["not a dict", {"code": "F401", "filename": "a.py", "message": "unused"}],
    )
    result = RuffJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok
    assert len(result.findings) == 1
    assert result.findings[0].location == "a.py"


def test_bandit_skips_non_dict_results_and_metrics(tmp_path: Path) -> None:
    payload = {
        "results": [
            "not a dict",
            {
                "filename": "src/a.py",
                "test_id": "B101",
                "issue_text": "Use of assert",
                "issue_severity": "LOW",
            },
        ],
        "metrics": {
            "_totals": "not a table",
            "src/a.py": {"SEVERITY.LOW": 1},
        },
    }
    _write_json(tmp_path / "bandit.json", payload)
    result = BanditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok
    assert len(result.findings) == 1
    # No line number: the location degrades to the filename alone.
    assert result.findings[0].location == "src/a.py"


def test_pip_audit_skips_dependencies_without_a_vulns_array(tmp_path: Path) -> None:
    payload = {
        "dependencies": [
            {"name": "clean", "version": "1.0"},
            {"name": "odd", "version": "1.0", "vulns": "not a list"},
            {"name": "bad", "version": "2.0", "vulns": [{"id": "GHSA-1"}]},
        ]
    }
    _write_json(tmp_path / "pip-audit.json", payload)
    result = PipAuditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok
    assert len(result.findings) == 1
    assert result.summary["vulnerable_packages"] == 1


def test_pip_audit_missing_dependencies_array_flips_parser_ok(tmp_path: Path) -> None:
    _write_json(tmp_path / "pip-audit.json", {"skipped": []})
    result = PipAuditJsonParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False
    assert any("dependencies" in n for n in result.notes)


def test_pylint_missing_messages_array_flips_parser_ok(tmp_path: Path) -> None:
    _write_json(tmp_path / "pylint.json", {"statistics": {}})
    result = PylintJsonParser().parse(ctx(tmp_path, rc=3))
    assert result.parser_ok is False
    assert any("`messages`" in n for n in result.notes)


def test_pylint_ignores_unusable_statistics(tmp_path: Path) -> None:
    payload = {
        "messages": [{"type": "error", "symbol": "e", "path": "a.py", "line": 1}],
        "statistics": {"messageTypeCount": "not a table", "score": "not a number"},
    }
    _write_json(tmp_path / "pylint.json", payload)
    result = PylintJsonParser().parse(ctx(tmp_path, rc=3, score_fail_under=9.0))
    # The count cross-check is skipped, but a non-numeric score is loud: the
    # gate cannot be evaluated, so the parse is not trustworthy.
    assert result.parser_ok is False
    assert any("not numeric" in n for n in result.notes)


def test_pytest_junit_declared_count_mismatch_flips_parser_ok(tmp_path: Path) -> None:
    xml = JUNIT_ONE_FAILURE.replace('failures="1"', 'failures="3"')
    (tmp_path / "junit.xml").write_text(xml, encoding="utf-8")
    result = PytestJUnitParser().parse(ctx(tmp_path, rc=1))
    assert result.parser_ok is False
    assert any("declares 3" in n for n in result.notes)


def test_coverage_propagates_a_junit_parse_mismatch(tmp_path: Path) -> None:
    (tmp_path / "coverage.xml").write_text(COVERAGE_XML, encoding="utf-8")
    (tmp_path / "junit.xml").write_text(
        JUNIT_ONE_FAILURE.replace('failures="1"', 'failures="3"'), encoding="utf-8"
    )
    result = CoverageXmlParser().parse(ctx(tmp_path, rc=1, fail_under=1.0))
    # A coverage gate that passed cannot launder an untrustworthy junit parse.
    assert result.parser_ok is False
    assert any("declares 3" in n for n in result.notes)


COVERAGE_XML_EDGE = """\
<?xml version="1.0" ?>
<coverage>
  <packages><package name="pkg">
    <classes>
      <class name="full.py" filename="src/pkg/full.py">
        <lines><line number="1" hits="1"/></lines>
      </class>
      <class name="dup.py" filename="src/pkg/mod.py">
        <lines><line number="1" hits="0"/></lines>
      </class>
      <class name="dup2.py" filename="src/pkg/mod.py">
        <lines><line number="2" hits="0"/></lines>
      </class>
      <class name="noname.py" filename="">
        <lines><line number="1" hits="0"/></lines>
      </class>
    </classes>
  </package></packages>
</coverage>
"""


def test_coverage_defaults_missing_rates_and_dedupes_files(tmp_path: Path) -> None:
    (tmp_path / "coverage.xml").write_text(COVERAGE_XML_EDGE, encoding="utf-8")
    result = CoverageXmlParser().parse(ctx(tmp_path, rc=0, fail_under=99.0))
    # No line-rate attribute at all: the default (0.0) is reported as-is
    # rather than invented, so the gate fails loudly instead of passing blind.
    assert result.summary["overall"]["line_percent"] == 0.0
    assert result.gate_failures
    files = [f["file"] for f in result.summary["top_uncovered_files"]]
    # Fully covered and unnamed classes are dropped; a repeated filename once.
    assert files == ["src/pkg/mod.py"]


def test_mypy_json_nonzero_without_errors_or_marker_flips_parser_ok(
    tmp_path: Path,
) -> None:
    result = MypyParser().parse(
        ctx(tmp_path, rc=2, log="mypy crashed somewhere\n", format="json")
    )
    assert result.parser_ok is False
    assert any("no errors were parsed" in n for n in result.notes)


def test_pre_commit_hook_line_without_a_name_is_not_a_hook(tmp_path: Path) -> None:
    log = "........................Passed\ncheck yaml...............Passed\n"
    result = PreCommitTextParser().parse(ctx(tmp_path, rc=0, log=log))
    assert result.parser_ok
    assert result.summary["hooks_total"] == 1
    assert result.summary["passed"] == 1


def test_pre_commit_failure_without_an_exit_code(tmp_path: Path) -> None:
    log = "black....................Failed\n- hook id: black\n"
    result = PreCommitTextParser().parse(ctx(tmp_path, rc=1, log=log))
    assert result.parser_ok
    assert [f.message for f in result.findings] == ["black failed"]


def test_artifact_path_reports_an_unresolvable_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_resolve = Path.resolve

    def _boom(self: Path, strict: bool = False) -> Path:
        if self.name == "junit.xml":
            raise OSError("ELOOP")  # a symlink loop, an unreadable mount, …
        return real_resolve(self, strict)

    monkeypatch.setattr(Path, "resolve", _boom)
    with pytest.raises(ArtifactPathError, match="could not be resolved"):
        artifact_path(tmp_path / "run", "junit.xml")


def test_pyright_unparsable_braces_flip_parser_ok(tmp_path: Path) -> None:
    # Braces are present, so the extractor tries -- and the slice is not JSON.
    result = PyrightJsonParser().parse(ctx(tmp_path, rc=1, log="npm error {oops}\n"))
    assert result.parser_ok is False
    assert any("could not extract" in n for n in result.notes)


def test_pyright_missing_general_diagnostics_flips_parser_ok(tmp_path: Path) -> None:
    result = PyrightJsonParser().parse(ctx(tmp_path, rc=1, log='{"summary": {}}'))
    assert result.parser_ok is False
    assert any("generalDiagnostics" in n for n in result.notes)


def test_bandit_unusable_results_entries_still_trip_the_guard(tmp_path: Path) -> None:
    """`results` is non-empty but nothing in it parses.

    That is a lost parse, not tool-side filtering: the abstention branch exists
    for a report whose ranks were cut, not for one the parser could not read.
    """
    payload = {
        "results": ["not-a-dict", 42],
        "metrics": {"_totals": _bandit_buckets(severity_high=2, confidence_high=2)},
    }
    _write_json(tmp_path / "bandit.json", payload)
    result = BanditJsonParser().parse(ctx(tmp_path, rc=0))
    assert result.parser_ok is False
    assert any("metrics imply 2 issue(s) but 0 were parsed" in n for n in result.notes)
