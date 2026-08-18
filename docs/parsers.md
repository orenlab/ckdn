---
icon: lucide/plug
---

# Parsers

Prefer machine-readable artifacts over terminal text.

| parser       | reads                                  | command must include                                                                                                                                                                   |
|--------------|----------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `pytest`     | JUnit XML                              | `--junitxml {run_dir}/junit.xml`                                                                                                                                                       |
| `coverage`   | coverage XML (+ JUnit if present)      | `--cov-report=xml:{run_dir}/coverage.xml` **and** `--junitxml {run_dir}/junit.xml` — without the latter, failing tests are not merged as findings and the parser says so in a note      |
| `ruff`       | JSON file                              | `--output-format json --output-file {run_dir}/ruff.json`                                                                                                                               |
| `ty`         | terminal text                          | — (drift guards)                                                                                                                                                                       |
| `mypy`       | text, or NDJSON with `format = "json"` | `--output json` (mypy ≥ 1.11) for NDJSON                                                                                                                                               |
| `pyright`    | JSON in log                            | `--outputjson`                                                                                                                                                                         |
| `reformat`   | black / ruff-format text               | `--check` (no `--diff`)                                                                                                                                                                |
| `pip_audit`  | JSON file                              | `--progress-spinner off -f json -o {run_dir}/pip-audit.json`; set a `timeout` (network tool). Skipped packages are a note, not a failure, and findings carry no `location`              |
| `bandit`     | JSON file                              | `-f json -o {run_dir}/bandit.json`                                                                                                                                                     |
| `pylint`     | json2 (pylint ≥ 3.0)                   | `--output-format=json2:{run_dir}/pylint.json` — every message class except `info` becomes a finding, refactor and convention included; narrow it in pylint's own config, not in ckdn    |
| `sarif`      | SARIF file                             | whatever flag writes SARIF to `{run_dir}/report.sarif` (semgrep `--sarif-output` **plus `--error`**, gitleaks `--report-format sarif --report-path`, trivy `--format sarif -o` **plus `--exit-code 1`**) |
| `pre_commit` | `pre-commit run` terminal text         | `pre-commit run` (use `--all-files` for full-repo parity); per-hook findings on failure                                                                                                |
| `generic`    | exit code only                         | — no findings by construction, so `rc ≠ 0` reconciles to `fail` with the log tail attached, never to `error`                                                                            |

**Severity mapping.** `ty`, `mypy` and `pyright` emit findings for **errors
only**; warnings are counted in `summary.warning_count` and never become
findings, because those tools exit 0 on warnings and a finding with `rc == 0`
reads as `parse_mismatch`. `mypy` `note:` lines fold into the preceding error's
`detail`, and are dropped when no error precedes them. `bandit`, `pip_audit`
and `pylint` (every class but `info`) emit each reported item as a finding.

**Guards (loud failure, never silent green):** self-consistency cross-checks
everywhere — `pytest`, `pyright`, `pylint` and `bandit` compare their report's
own declared counts against what was parsed, and the text parsers (`ty`,
`mypy`, `reformat`, `pre_commit`) check counts and clean markers. A missing or
unreadable report is `error` when `rc ≠ 0` and **`parse_mismatch` when
`rc == 0`** — the more surprising half. `parser_ok=false` on format drift.

**Policy gates in ckdn config:** `fail_under` (coverage) and `score_fail_under`
(pylint). Each turns a run red even when the tool exits 0, and each is **opt-in
and silently skipped when unset** — a `coverage` check with no `fail_under`
reports `pass` at any percentage, with a note saying the gate was skipped.

SARIF's `fail_levels` is **not** a gate: it selects which result levels become
findings and can never produce a gate failure. It defaults to `["error"]`, and a
result carrying no `level` counts as `warning` — so a tool that exits 0 while
reporting warnings yields zero findings and `status: pass`. Widening it to
`fail_levels = ["error", "warning"]` is only half the job: the tool must exit
nonzero on those warnings too, or the findings contradict `rc == 0` and the run
reports `parse_mismatch`. Make the tool fail first (`--error`, `--exit-code 1`),
then widen `fail_levels` to match.

Filter severity tool-side where possible (bandit `--severity-level`) — a parser
must never hide findings the exit code knows about.

**Artifact options.** Every file-backed parser takes an artifact-path option
(`{run_dir}` is substituted): `junit` (`pytest`, `coverage`), `coverage_xml`
(`coverage`), `report` (`ruff`, `bandit`, `pip_audit`, `pylint`, `sarif`). Paths
resolve strictly under the run directory; anything escaping it is rejected
before the file is opened.

**Not supported on purpose:** flake8 / isort / pydocstyle / pyupgrade (use
ruff); vulture (overlaps [CodeClone](https://github.com/orenlab/codeclone)'s
structural dead-code analysis); safety (use pip-audit); mutmut-style mutation
as a loop-time check.

## Custom parsers

A parser reports facts; it never decides the final status.

A `Finding` carries `id`, `kind` and `message` (all required), plus optional
`location` — one preformatted string, `path`, `path:line` or `path:line:column`
— and `detail`, a bounded tuple of context lines. There is **no `severity`
field**: encode it in `kind` or `detail`. An absent `location` or `detail` is
omitted from the digest entirely.

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

Rules: prefer `{run_dir}` artifacts (`ctx.artifact()` refuses any path that
escapes the run directory); if parsing text, add a self-consistency guard;
findings = failure evidence only; bound everything; return `parser_ok=False`
instead of raising on bad output — a parser that raises anyway is caught and
recorded as `parser_ok=false` with a `crashed` note, never as a lost run.

### Registration

=== "Entry point (installed package)"

    Expose the parser under the `ckdn.parsers` entry-point group; ckdn
    discovers it once per process (the lookup is cached) and resolves it
    lazily. The value may be a `Parser` class (instantiated with no args) or an
    instance. Built-in names take precedence and are never shadowed, two
    plugins claiming the same name resolve first-discovered-wins, and a plugin
    that fails to import is skipped rather than breaking ckdn.

    ```toml
    # pyproject.toml of your parser package
    [project.entry-points."ckdn.parsers"]
    mytool = "my_pkg:MyToolParser"
    ```

=== "Fork-and-own"

    Import the parser in `ckdn/parsers/__init__.py` and add an instance to the
    tuple `_REGISTRY` is built from — the dict itself is a comprehension keyed
    by `parser.name`.
