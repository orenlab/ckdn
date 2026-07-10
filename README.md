<!--
SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
SPDX-License-Identifier: MIT
-->
<div align="center">

  <picture>
    <source
      media="(prefers-color-scheme: dark)"
      srcset="https://raw.githubusercontent.com/orenlab/ckdn/main/assets/ckdn-wordmark-dark.svg"
    >
    <source
      media="(prefers-color-scheme: light)"
      srcset="https://raw.githubusercontent.com/orenlab/ckdn/main/assets/ckdn-wordmark.svg"
    >
    <img
      alt="ckdn — deterministic check runner and log digester for AI-assisted development loops"
      src="https://raw.githubusercontent.com/orenlab/ckdn/main/assets/ckdn-wordmark.svg"
      width="280"
    >
  </picture>

  <p><strong>Deterministic check runner and log digester for AI-assisted development loops</strong></p>

  <p>
    <em>
      Let agents move fast.<br>
      Keep verification explicit, bounded, and machine-readable.
    </em>
  </p>

  <p>
    <a href="https://pypi.org/project/ckdn/"><img src="https://img.shields.io/pypi/v/ckdn.svg" alt="PyPI"/></a>
    <a href="https://github.com/orenlab/ckdn/actions/workflows/ci.yml"><img src="https://github.com/orenlab/ckdn/actions/workflows/ci.yml/badge.svg" alt="CI"/></a>
    <a href="https://github.com/orenlab/ckdn/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-3dd68c" alt="MIT license"/></a>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-%3E%3D3.11-3776AB?logo=python&logoColor=white" alt="Python ≥ 3.11"/></a>
    <a href="https://github.com/orenlab/ckdn"><img src="https://img.shields.io/badge/deps-stdlib%20only-0f1a17" alt="stdlib only"/></a>
    <a href="https://github.com/orenlab/ckdn/security/advisories"><img src="https://img.shields.io/badge/security-policy-7ee0b0" alt="Security policy"/></a>
    <a href="#digests-ckdndigest2"><img src="https://img.shields.io/badge/digest-ckdn.digest%2F2-7ee0b0" alt="digest schema v2"/></a>
  </p>

</div>

---

**ckdn** (short for **checkdown**) sits between a coding agent and your
project’s verification tools. The agent never reads a 10 000-line pytest
log and never decides from prose whether a run “looks green”.

Every check goes through one orchestrator that:

1. owns the true process exit code,
2. archives the full log as evidence,
3. emits a **bounded, machine-readable digest** — the only thing the agent
   is supposed to read.

```
agent ──> ckdn run coverage ──> subprocess (owns exit code)
                                 ├── .agent-runs/<ts>-coverage/full.log
                                 ├── .agent-runs/<ts>-coverage/coverage.xml
                                 ├── .agent-runs/<ts>-coverage/meta.json
                                 └── .agent-runs/<ts>-coverage/digest.json   ← agent reads this
```

Runtime: Python ≥ 3.11, **stdlib only** (zero third-party dependencies).

## Why

Letting an agent interpret raw tool output fails in two directions:

1. **Context bloat** — a full coverage run with `term-missing` is thousands
   of lines. The agent burns context on noise and misses the signal.
2. **False green** — text-based interpretation invites the worst failure
   mode: a collection error produces no `FAILED` lines, a regex finds
   nothing, and the run is reported clean.

ckdn’s answer is a strict status model from *both* the exit code and a
format-aware parser. The two must agree before anything is called green.
ckdn may **downgrade** green; it never **upgrades** red.

## Install

```bash
uv tool install ckdn          # global CLI
# or as a project dev dependency:
uv add --dev ckdn
```

## Quick start

```bash
cd your-project
ckdn init                      # writes starter ckdn.toml
# edit commands / parsers / aliases to match the project
echo '.agent-runs/' >> .gitignore

ckdn checks                    # list configured checks
ckdn run lint                  # alias → members (e.g. ruff)
ckdn run ruff                  # one atomic check
ckdn show                      # pretty-print latest digest
ckdn list                      # recent runs
```

## Status model

Every run reconciles exit code (`rc`) against the parser into exactly one
status. **`pass` is the only green state.**

| rc | parser | status | meaning |
|----|--------|--------|---------|
| 0 | confident, no findings, gates ok | `pass` | green |
| 0 | gate failed (e.g. coverage &lt; `fail_under`) | `fail` | tool happy, policy not |
| ≠ 0 | findings extracted | `fail` | normal red + evidence |
| ≠ 0 | no findings, evidence expected | `error` | infra / collection — fix the run |
| ≠ 0 | could not interpret output | `error` | same, with log tail |
| 0 | findings anyway / unreadable | `parse_mismatch` | green untrusted |

Invariants (enforced by `ckdn.reconcile`, covered by contract tests):

- Text never upgrades a nonzero exit code to green.
- A zero exit code never survives contradicting evidence.
- A confused parser sets `parser_ok=false` → loud `error` /
  `parse_mismatch`, never a silent clean.

`ckdn run` exits with the original command’s code (clamped 1–255). Extra
rule: `rc == 0` with a non-green status exits `1`.

## Digests (`ckdn.digest/2`)

Stdout and on-disk `digest.json` are **compact** and **sparse**: absent
keys mean empty / `0` / `false`. Always present: `schema`, `check`,
`status`, `rc`, `run_dir`.

Green pass (intentionally tiny):

```json
{"schema":"ckdn.digest/2","check":"ruff","status":"pass","rc":0,"run_dir":".agent-runs/20260707T101500Z-ruff"}
```

Failure keeps the evidence (`status_reason`, findings, gates, notes,
truncation, artifacts, optional `log_tail`). `ckdn show` re-indents a
stored digest for humans.

`digest.json` is deterministic (no timestamps / durations — those live in
`meta.json`). Digests carry **facts only**; policy belongs in a skill or
`CLAUDE.md`, not in the data file.

## Configuration

`ckdn.toml` at the project root (`ckdn init`; override with `--config`).
Excerpt of the starter (full catalogue is written by `ckdn init`):

```toml
[run]
runs_dir = ".agent-runs"
keep = 20
top = 20
max_snippet_lines = 12
log_tail_lines = 40

[check.pytest]
command = "uv run pytest -q --junitxml {run_dir}/junit.xml"
parser = "pytest"

[check.coverage]
command = "uv run pytest -q --junitxml {run_dir}/junit.xml --cov=src --cov-report=term-missing --cov-report=xml:{run_dir}/coverage.xml"
parser = "coverage"
fail_under = 96.0

[check.ty]
command = "uvx ty check"
parser = "ty"

[check.mypy]
command = "uv run mypy src --output json"
parser = "mypy"
format = "json"

[check.types]
members = ["ty", "mypy"]         # alias → atomic members in order

[check.ruff]
command = "uv run ruff check --output-format json --output-file {run_dir}/ruff.json ."
parser = "ruff"

[check.lint]
members = ["ruff"]               # add pylint / bandit / … when enabled
# fail_fast = true               # default; false runs all members
```

**Atomic** check: `command` + `parser` (required), optional `timeout`.
Other keys are parser options (`fail_under`, `score_fail_under`, …).

**Alias**: `members = ["atomic", …]` only (optional `fail_fast`, default
`true`). No nesting. `ckdn run lint` runs each member (own run dir +
digest) and prints a sparse aggregate on stdout. Extra args after `--`
are rejected on aliases — pass them to the atomic check
(`ckdn run ruff -- …`).

Commands are tokenized with `shlex` and run **without a shell** (no
pipes, no `&&`). `{run_dir}` is substituted in commands and artifact
paths — point machine-readable reports into the run directory.

## CLI

| Command | Purpose |
|---------|---------|
| `ckdn run <check> [--quiet] [-- extra…]` | run check / alias; compact digest on stdout |
| `ckdn show [run-dir]` | pretty-print a stored digest (latest default) |
| `ckdn list [-n N]` | recent runs |
| `ckdn checks` | configured checks (atomics + aliases) |
| `ckdn gc [--keep N]` | prune old run directories |
| `ckdn init` | write starter `ckdn.toml` |

Alias stdout is **only** the aggregate; member digests stay under
`.agent-runs/` for `ckdn show`.

## Run directory

```
.agent-runs/
  20260707T101500Z-ruff/
    full.log      # interleaved stdout+stderr
    ruff.json     # tool artifact via {run_dir}
    meta.json     # argv, rc, timestamps, duration, log sha256
    digest.json   # deterministic facts for the reader
  latest -> 20260707T101500Z-ruff
```

`.agent-runs/` is evidence: do not edit it; keep it out of version control.

## Built-in parsers

Prefer machine-readable artifacts over terminal text.

| parser | reads | command must include |
|--------|-------|----------------------|
| `pytest` | JUnit XML | `--junitxml {run_dir}/junit.xml` |
| `coverage` | coverage XML (+ JUnit if present) | `--cov-report=xml:{run_dir}/coverage.xml` |
| `ruff` | JSON file | `--output-format json --output-file {run_dir}/ruff.json` |
| `ty` | terminal text | — (drift guards) |
| `mypy` | text or NDJSON | optional `format = "json"` + `--output json` |
| `pyright` | JSON in log | `--outputjson` |
| `reformat` | black / ruff-format text | `--check` (no `--diff`) |
| `pip_audit` | JSON file | `-f json -o {run_dir}/pip-audit.json` |
| `bandit` | JSON file | `-f json -o {run_dir}/bandit.json` |
| `pylint` | json2 (pylint ≥ 3.0) | `--output-format=json2:{run_dir}/pylint.json` |
| `sarif` | SARIF file | `--sarif-output {run_dir}/report.sarif` |
| `generic` | exit code only | — |

**Guards (loud failure, never silent green):** count / clean-marker
cross-checks on text parsers; missing reports with `rc ≠ 0` → `error`;
`parser_ok=false` on format drift.

**Policy gates in ckdn config:** `fail_under` (coverage),
`score_fail_under` (pylint). Filter severity tool-side where possible
(bandit, SARIF `fail_levels`).

**Not supported on purpose:** flake8 / isort / pydocstyle / pyupgrade
(use ruff); vulture (overlaps structural analysis elsewhere); safety
(use pip-audit); mutmut-style mutation as a loop-time check.

## Agent integration

Three layers, increasing strength:

1. **Standing rule** (`CLAUDE.md` / equivalent) — run only via
   `ckdn run <check>`; read the digest; `pass` is the only green; never
   edit `.agent-runs/` or weaken checks to go green.
2. **Skill** — `examples/claude/skills/verified-fix-loop/SKILL.md`
   (copy into the agent’s skills dir). Bounded fix loop, digest-only
   reading, forbidden moves.
3. **Hooks / CI** — `ckdn run` passes red exit codes through, so it drops
   into the same slots as the raw tool, with digests as a side effect.

Division of labor: constitution → procedure → instrumentation →
enforcement. Digests never contain instructions to the agent (prompt-
injection surface and policy fork).

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

Rules: prefer `{run_dir}` artifacts; if parsing text, add a
self-consistency guard; findings = failure evidence only; bound
everything; return `parser_ok=False` instead of raising on bad output.

Registration today: edit `_REGISTRY` in `ckdn/parsers/__init__.py`
(fork-and-own; no entry-point plugin API yet).

## Design principles

- **Exit-code-first** — parsing only makes the verdict stricter.
- **Agree-or-alarm** — `pass` requires exit code and parser to agree.
- **Reports over regexes** — JUnit / coverage XML / JSON where possible.
- **Facts ≠ policy** — digests vs skills / project rules.
- **Determinism where it pays** — digest vs meta split.
- **No shell** — exit codes are not laundered through pipelines.
- **Stdlib only** — the guard of dependency behavior brings none of its own.

## Non-goals (for now)

- Parallel member execution and global `ckdn run --all` (named aliases
  cover lint/types groups; full-suite sequencing stays with the caller)
- Watch mode, TUI, HTML dashboards
- Windows symlink handling beyond the `LATEST` marker fallback
- Pluggable parser entry points

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests
uv run mypy src/ckdn
```

Entry point: `ckdn` → `ckdn.cli:main`.

Contract tests pin the status-model invariants; parser tests pin fact
extraction and loud-failure guards.

## License & community

- Copyright (c) 2026 Den Rozhnovskiy \<rozhnovskiydenis@gmail.com\>
- License: [MIT](LICENSE) (`SPDX-License-Identifier: MIT`)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Security: [SECURITY.md](SECURITY.md)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Code of Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
