<!--
SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
SPDX-License-Identifier: MIT
-->
<div align="center">

  <picture>
    <source
      media="(prefers-color-scheme: dark)"
      srcset="https://raw.githubusercontent.com/orenlab/ckdn/refs/heads/main/assets/ckdn-wordmark-dark.svg"
    >
    <source
      media="(prefers-color-scheme: light)"
      srcset="https://raw.githubusercontent.com/orenlab/ckdn/refs/heads/main/assets/ckdn-wordmark.svg"
    >
    <img
      alt="ckdn — deterministic check runner and log digester for AI-assisted development loops"
      src="https://raw.githubusercontent.com/orenlab/ckdn/refs/heads/main/assets/ckdn-wordmark.svg"
      width="220"
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
    <a href="https://pypi.org/project/ckdn/"><img src="https://img.shields.io/pypi/v/ckdn.svg?color=3dd68c&logo=pypi&logoColor=white" alt="PyPI"/></a>
    <a href="https://github.com/orenlab/ckdn/actions/workflows/ci.yml"><img src="https://github.com/orenlab/ckdn/actions/workflows/ci.yml/badge.svg" alt="CI"/></a>
    <a href="https://github.com/orenlab/ckdn/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-3dd68c?logo=opensourceinitiative&logoColor=white" alt="MIT license"/></a>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-%3E%3D3.11-3dd68c?logo=python&logoColor=white" alt="Python ≥ 3.11"/></a>
    <a href="https://github.com/orenlab/ckdn"><img src="https://img.shields.io/badge/core-stdlib%20only-3dd68c?logo=python&logoColor=white" alt="core: stdlib only"/></a>
    <a href="https://github.com/orenlab/ckdn/security/advisories"><img src="https://img.shields.io/badge/security-policy-3dd68c?logo=github&logoColor=white" alt="Security policy"/></a>
    <a href="#digests-ckdndigest2"><img src="https://img.shields.io/badge/digest-ckdn.digest%2F2-3dd68c?logo=json&logoColor=white" alt="digest schema v2"/></a>
  </p>

</div>

---

**ckdn** (short for **checkdown**) sits between a coding agent and your
project’s verification tools. The agent never reads a 10 000-line pytest
log and never decides from prose whether a run “looks green”.

Every check goes through one orchestrator that:

1. owns the true process exit code,
2. archives the full log as evidence,
3. emits a **bounded, machine-readable digest** — the only thing the agent
   is supposed to read.

<div align="center">
  <picture>
    <source
      media="(prefers-color-scheme: dark)"
      srcset="https://raw.githubusercontent.com/orenlab/ckdn/refs/heads/main/assets/ckdn-pipeline-dark.svg"
    >
    <img
      alt="ckdn pipeline: agent → ckdn run coverage → subprocess (owns exit code) → .agent-runs artifacts; agent reads digest.json"
      src="https://raw.githubusercontent.com/orenlab/ckdn/refs/heads/main/assets/ckdn-pipeline.svg"
      width="680"
    >
  </picture>
</div>

Runtime: Python ≥ 3.11, **stdlib only** for the core CLI (zero third-party
dependencies). The optional [MCP server](#mcp-optional) is an extra
(`ckdn[mcp]`).

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

The core has zero dependencies. The MCP transport is an optional extra —
see [MCP](#mcp-optional).

## Quick start

```bash
cd your-project
ckdn init                      # writes starter ckdn.toml
# edit commands / parsers / aliases to match the project
echo '.agent-runs/' >> .gitignore

ckdn checks                    # list configured checks
ckdn run ruff                  # one atomic check
ckdn run lint                  # alias → members (e.g. ruff, pylint)
ckdn show                      # pretty-print latest digest
ckdn list                      # recent runs
```

## Status model

Every run reconciles exit code (`rc`) against the parser into exactly one
status. **`pass` is the only green state.**

| rc  | parser                                        | status           | meaning                          |
|-----|-----------------------------------------------|------------------|----------------------------------|
| 0   | confident, no findings, gates ok              | `pass`           | green                            |
| 0   | gate failed (e.g. coverage &lt; `fail_under`) | `fail`           | tool happy, policy not           |
| ≠ 0 | findings extracted                            | `fail`           | normal red + evidence            |
| ≠ 0 | no findings, evidence expected                | `error`          | infra / collection — fix the run |
| ≠ 0 | could not interpret output                    | `error`          | same, with log tail              |
| 0   | findings anyway / unreadable                  | `parse_mismatch` | green untrusted                  |

Invariants (enforced by `ckdn.reconcile`, covered by contract tests):

- Text never upgrades a nonzero exit code to green.
- A zero exit code never survives contradicting evidence.
- A confused parser sets `parser_ok=false` → loud `error` /
  `parse_mismatch`, never a silent clean.

**Exit-code contract.** `ckdn run` exits with the original command’s code
(clamped 1–255), so it drops into any hook or CI slot where the raw
command used to be. One extra rule: `rc == 0` with a non-green status
exits `1`.

## Digests (`ckdn.digest/2`)

Stdout and on-disk `digest.json` are **compact** and **sparse**: absent
keys mean empty / `0` / `false`. Always present: `schema`, `check`,
`status`, `rc`, `run_dir`.

Green pass (intentionally tiny):

```json
{
  "schema": "ckdn.digest/2",
  "check": "ruff",
  "status": "pass",
  "rc": 0,
  "run_dir": ".agent-runs/20260707T101500Z-ruff"
}
```

Failure keeps the evidence — bounded findings with locations and
snippets, gates, notes, explicit truncation counters (shown indented here
for readability; `ckdn show` does the same for stored digests):

<details>
<summary>Failure digest — full shape (findings, summary, artifacts)</summary>

```json
{
  "schema": "ckdn.digest/2",
  "check": "pytest",
  "status": "fail",
  "status_reason": "exit code 1 with 1 finding(s)",
  "rc": 1,
  "summary": {
    "counts": {
      "tests": 214,
      "failures": 1,
      "skipped": 2
    }
  },
  "findings_total": 1,
  "findings": [
    {
      "id": "tests.test_digest::test_sparse_keys",
      "kind": "test_failure",
      "message": "assert 'notes' not in digest",
      "location": "tests/test_digest.py:41",
      "detail": [
        "E       AssertionError: assert 'notes' not in digest"
      ]
    }
  ],
  "run_dir": ".agent-runs/20260707T101500Z-pytest",
  "artifacts": [
    "full.log",
    "junit.xml",
    "meta.json"
  ]
}
```
</details>

On `error` / `parse_mismatch` the digest additionally carries a bounded
`log_tail`.

`digest.json` is deterministic (no timestamps / durations — those live in
`meta.json`). Digests carry **facts only**; policy belongs in a skill or
`CLAUDE.md`, not in the data file.

## Aliases and aggregates (`ckdn.aggregate/1`)

An alias groups atomic checks: `ckdn run lint` runs each member in config
order. Every member gets its **own run directory and digest** — the
aggregate on stdout is a routing document, not a replacement for member
evidence:

<details>
<summary>Aggregate — <code>ckdn.aggregate/1</code> example</summary>

```json
{
  "schema": "ckdn.aggregate/1",
  "alias": "lint",
  "status": "fail",
  "rc": 1,
  "members": [
    {
      "check": "ruff",
      "status": "fail",
      "rc": 1,
      "run_dir": ".agent-runs/20260707T101500Z-ruff"
    },
    {
      "check": "pylint",
      "status": "skipped"
    }
  ]
}
```
</details>

The aggregate contract:

- `status` — `pass` iff every member passed; otherwise the first
  non-green member’s status.
- `rc` (also the process exit code) — follows the same pass-through
  rule as atomic runs: the first non-green member’s exit code, or `1`
  if that member’s own `rc` was `0` (gate failure / mismatch).
- `fail_fast = true` (default) stops at the first non-green member;
  members not reached are listed as `"skipped"`. With
  `fail_fast = false` all members run and every entry carries a real
  status.
- Extra args after `--` are rejected on aliases — pass them to the
  atomic check (`ckdn run ruff -- -x`).

Read the aggregate to decide *which* member digest to open
(`ckdn show <run-dir>`), then work from that digest.

## Configuration

`ckdn.toml` at the project root (`ckdn init`; override with `--config`).
Subprocesses and relative `runs_dir` paths resolve from the **invocation
working directory** (`--cwd` or `CKDN_CWD`), not from the config file's
parent — so a config copied to `/tmp` can drive checks in a git worktree.
Excerpt of the starter (the full catalogue is written by `ckdn init`):

<details>
<summary><code>ckdn.toml</code> — starter excerpt (atomics + aliases)</summary>

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
command = "uv run ty check"
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

[check.format]
command = "uv run ruff format --check ."
parser = "reformat"

[check.pre_commit]
command = "uv run pre-commit run --all-files"
parser = "pre_commit"

[check.lock]
command = "uv lock --check"
parser = "generic"

[check.style]
members = ["format", "ruff"]     # format + lint atomics

[check.hooks]
members = ["pre_commit"]           # full hook suite
```
</details>

**Atomic** check: `command` + `parser` (required), optional `timeout`
in seconds (a timeout yields `rc=124` and a non-green status). Any other
key is passed to the parser as an option (`fail_under`,
`score_fail_under`, `fail_levels`, …).

**Alias**: `members = ["atomic", …]` only (optional `fail_fast`). No
nesting.

Commands are tokenized with `shlex` and run **without a shell** — no
pipes, no redirects, no `&&`. Deliberate: a shell pipeline is exactly
where exit codes get laundered (`cmd | tee` reports tee’s status). If a
check needs shell features, wrap them in a script and point `command` at
it. `{run_dir}` is substituted in commands and artifact paths — point
machine-readable reports into the run directory.

**Command policy** (default `workspace`): before any subprocess starts,
path-like argv tokens must resolve inside the invocation `cwd`
(`--cwd` / `CKDN_CWD`). `/etc/passwd`, `..` escapes, and paths under
`/etc`, `/proc`, `~/.ssh`, etc. are rejected. MCP `extra_args` are
subject to the same rules. Set `command_policy = "allowlist"` to require
configured command prefixes (`uv run `, `uvx `, …, or custom
`[run.command_allowlist].prefixes`). Use `command_policy = "off"` only
for exotic workflows. CI: `ckdn lock-config` then
`ckdn verify-config --locked` catches tampered commands without running
them.

## CLI

Global flags (on commands that load config): `--config PATH`, `--cwd DIR`
(working directory for subprocesses and relative `runs_dir`; else `CKDN_CWD`).

| Command                                  | Purpose                                                         |
|------------------------------------------|-----------------------------------------------------------------|
| `ckdn run <check> [--quiet] [-- extra…]` | run atomic check or alias; compact digest / aggregate on stdout |
| `ckdn show [run-dir]`                    | pretty-print a stored digest (latest default)                   |
| `ckdn list [-n N]`                       | recent runs                                                     |
| `ckdn checks`                            | configured checks (atomics + aliases)                           |
| `ckdn gc [--keep N]`                     | prune old run directories                                       |
| `ckdn init`                              | write starter `ckdn.toml`                                       |
| `ckdn verify-config [--locked]`          | validate command policy (+ optional `ckdn.lock.toml`)           |
| `ckdn lock-config [-o path]`             | write command SHA-256 lock file for CI                          |

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

| parser      | reads                                  | command must include                                                                                                                                                                   |
|-------------|----------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `pytest`    | JUnit XML                              | `--junitxml {run_dir}/junit.xml`                                                                                                                                                       |
| `coverage`  | coverage XML (+ JUnit if present)      | `--cov-report=xml:{run_dir}/coverage.xml`                                                                                                                                              |
| `ruff`      | JSON file                              | `--output-format json --output-file {run_dir}/ruff.json`                                                                                                                               |
| `ty`        | terminal text                          | — (drift guards)                                                                                                                                                                       |
| `mypy`      | text, or NDJSON with `format = "json"` | `--output json` (mypy ≥ 1.11) for NDJSON                                                                                                                                               |
| `pyright`   | JSON in log                            | `--outputjson`                                                                                                                                                                         |
| `reformat`  | black / ruff-format text               | `--check` (no `--diff`)                                                                                                                                                                |
| `pip_audit` | JSON file                              | `-f json -o {run_dir}/pip-audit.json`                                                                                                                                                  |
| `bandit`    | JSON file                              | `-f json -o {run_dir}/bandit.json`                                                                                                                                                     |
| `pylint`    | json2 (pylint ≥ 3.0)                   | `--output-format=json2:{run_dir}/pylint.json`                                                                                                                                          |
| `sarif`     | SARIF file                             | whatever flag writes SARIF to `{run_dir}/report.sarif` (semgrep `--sarif-output`, gitleaks `--report-format sarif --report-path`, trivy `--format sarif -o`); artifact option `report` |
| `pre_commit` | `pre-commit run` terminal text         | `pre-commit run` (use `--all-files` for full-repo parity); per-hook findings on failure                                                                                                  |
| `generic`   | exit code only                         | —                                                                                                                                                                                      |

**Guards (loud failure, never silent green):** count / clean-marker
cross-checks on text parsers; missing reports with `rc ≠ 0` → `error`;
`parser_ok=false` on format drift.

**Policy gates in ckdn config:** `fail_under` (coverage),
`score_fail_under` (pylint), `fail_levels` (SARIF). Filter severity
tool-side where possible (bandit `--severity-level`) — a parser must
never hide findings the exit code knows about.

**Not supported on purpose:** flake8 / isort / pydocstyle / pyupgrade
(use ruff); vulture (overlaps
[CodeClone](https://github.com/orenlab/codeclone)’s structural dead-code
analysis); safety (use pip-audit); mutmut-style mutation as a loop-time
check.

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

## MCP (optional)

The fourth integration path: when an agent should call ckdn over MCP
instead of shelling out, install the FastMCP transport:

```bash
uv tool install 'ckdn[mcp]'
```

`ckdn-mcp` speaks **stdio** only. Config resolution: `--config` →
`$CKDN_CONFIG` → `./ckdn.toml` (cwd). Working directory: `--cwd` →
`$CKDN_CWD` → process cwd. Every client shares the schema
`{ command, args, env }`; only the file name and format differ.

<details>
<summary><b>Claude Code</b> — <code>.mcp.json</code> (project-scoped, committed)</summary>

```bash
claude mcp add --scope project ckdn -- ckdn-mcp
```

or commit a `.mcp.json` at the repo root (Claude Code expands `${VAR}`):

```json
{
  "mcpServers": {
    "ckdn": {
      "command": "ckdn-mcp",
      "args": [],
      "env": {
        "CKDN_CONFIG": "${CKDN_CONFIG:-ckdn.toml}"
      }
    }
  }
}
```

</details>

<details>
<summary><b>Cursor</b> — <code>.cursor/mcp.json</code> (or global <code>~/.cursor/mcp.json</code>)</summary>

```json
{
  "mcpServers": {
    "ckdn": {
      "command": "ckdn-mcp",
      "args": [],
      "env": {
        "CKDN_CONFIG": "/absolute/path/to/ckdn.toml"
      }
    }
  }
}
```

</details>

<details>
<summary><b>Claude Desktop</b> — <code>claude_desktop_config.json</code></summary>

Settings → Developer → Edit Config, same schema:

```json
{
  "mcpServers": {
    "ckdn": {
      "command": "ckdn-mcp",
      "args": [],
      "env": {
        "CKDN_CONFIG": "/absolute/path/to/ckdn.toml"
      }
    }
  }
}
```

</details>

<details>
<summary><b>ChatGPT Codex</b> — <code>~/.codex/config.toml</code> (TOML, not JSON)</summary>

```toml
[mcp_servers.ckdn]
command = "ckdn-mcp"
args = []
env = { CKDN_CONFIG = "/absolute/path/to/ckdn.toml" }
```

</details>

Tools (thin adapter over the same application layer as the CLI):

| Tool           | Purpose                                                               |
|----------------|-----------------------------------------------------------------------|
| `list_checks`  | Configured atomic checks + aliases                                    |
| `run_check`    | Run one **atomic** check → `{digest, exit_code}`                      |
| `run_group`    | Run one **alias** → `{aggregate, exit_code}`                          |
| `get_digest`   | Load stored `ckdn.digest/2` (latest or by run id)                     |
| `list_runs`    | Recent run summaries                                                  |
| `get_evidence` | Bounded findings / artifact line slices (never auto-dumps `full.log`) |

Trust rules:

- Only checks from `ckdn.toml` — no arbitrary shell.
- `fail` / `error` / `parse_mismatch` are **normal structured results**,
  not MCP tool failures.
- MCP `isError` is reserved for impossible tool calls (missing config,
  unknown check, path escape).
- `run` is a run id (single directory name), never a path; refs that escape
  `.agent-runs/` are `isError`, not silent reads.
- `exit_code` in tool results is a convenience mirror of the digest’s
  `rc`; the digest is the source of truth.
- Core CLI remains stdlib-only; FastMCP is the optional extra.

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
uv run mypy src
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
