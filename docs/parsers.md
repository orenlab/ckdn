---
icon: lucide/plug
---

# Parsers

Prefer machine-readable artifacts over terminal text.

| parser       | reads                                  | command must include                                                                                                                                                                   |
|--------------|----------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `pytest`     | JUnit XML                              | `--junitxml {run_dir}/junit.xml`                                                                                                                                                       |
| `coverage`   | coverage XML (+ JUnit if present)      | `--cov-report=xml:{run_dir}/coverage.xml`                                                                                                                                              |
| `ruff`       | JSON file                              | `--output-format json --output-file {run_dir}/ruff.json`                                                                                                                               |
| `ty`         | terminal text                          | — (drift guards)                                                                                                                                                                       |
| `mypy`       | text, or NDJSON with `format = "json"` | `--output json` (mypy ≥ 1.11) for NDJSON                                                                                                                                               |
| `pyright`    | JSON in log                            | `--outputjson`                                                                                                                                                                         |
| `reformat`   | black / ruff-format text               | `--check` (no `--diff`)                                                                                                                                                                |
| `pip_audit`  | JSON file                              | `-f json -o {run_dir}/pip-audit.json`                                                                                                                                                  |
| `bandit`     | JSON file                              | `-f json -o {run_dir}/bandit.json`                                                                                                                                                     |
| `pylint`     | json2 (pylint ≥ 3.0)                   | `--output-format=json2:{run_dir}/pylint.json`                                                                                                                                          |
| `sarif`      | SARIF file                             | whatever flag writes SARIF to `{run_dir}/report.sarif` (semgrep `--sarif-output`, gitleaks `--report-format sarif --report-path`, trivy `--format sarif -o`); artifact option `report` |
| `pre_commit` | `pre-commit run` terminal text         | `pre-commit run` (use `--all-files` for full-repo parity); per-hook findings on failure                                                                                                |
| `generic`    | exit code only                         | —                                                                                                                                                                                      |

**Guards (loud failure, never silent green):** count / clean-marker
cross-checks on text parsers; missing reports with `rc ≠ 0` → `error`;
`parser_ok=false` on format drift.

**Policy gates in ckdn config:** `fail_under` (coverage), `score_fail_under`
(pylint), `fail_levels` (SARIF). Filter severity tool-side where possible
(bandit `--severity-level`) — a parser must never hide findings the exit code
knows about.

**Not supported on purpose:** flake8 / isort / pydocstyle / pyupgrade (use
ruff); vulture (overlaps [CodeClone](https://github.com/orenlab/codeclone)'s
structural dead-code analysis); safety (use pip-audit); mutmut-style mutation
as a loop-time check.

## Custom parsers

A parser reports facts; it never decides the final status.

```python
from ckdn.parsers.base import Finding, ParseContext, ParseResult


class MyToolParser:
  name = "mytool"

  def parse(self, ctx: ParseContext) -> ParseResult:
    report = ctx.artifact("report", "mytool.json")
    if not report.exists():
      return ParseResult(
        parser_ok=False,
        notes=[f"report not found: {report}"],
      )
    return ParseResult(findings=[...], summary={"count": 0})
```

Rules: prefer `{run_dir}` artifacts; if parsing text, add a self-consistency
guard; findings = failure evidence only; bound everything; return
`parser_ok=False` instead of raising on bad output.

### Registration

=== "Entry point (installed package)"

    Expose the parser under the `ckdn.parsers` entry-point group; ckdn
    discovers it at runtime. The value may be a `Parser` class (instantiated
    with no args) or an instance. Built-in names take precedence and are never
    shadowed, and a plugin that fails to import is skipped rather than breaking
    ckdn.

    ```toml
    # pyproject.toml of your parser package
    [project.entry-points."ckdn.parsers"]
    mytool = "my_pkg:MyToolParser"
    ```

=== "Fork-and-own"

    Add the instance to `_REGISTRY` in `ckdn/parsers/__init__.py`.
