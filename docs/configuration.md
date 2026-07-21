---
icon: lucide/settings
---

# Configuration

`ckdn.toml` at the project root (`ckdn init`; override with `--config`).
Subprocesses and relative `runs_dir` paths resolve from the **invocation
working directory** (`--cwd` or `CKDN_CWD`), not from the config file's parent
— so a config copied to `/tmp` can drive checks in a git worktree.

Excerpt of the starter (the full catalogue is written by `ckdn init`):

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

[check.mypy]
command = "uv run mypy --output json"
parser = "mypy"
format = "json"

[check.ruff]
command = "uv run ruff check --output-format json --output-file {run_dir}/ruff.json ."
parser = "ruff"

[check.lint]
members = ["ruff"]               # add pylint / bandit / … when enabled

[check.style]
members = ["format", "ruff"]     # format + lint atomics
```

## Checks

**Atomic** check: `command` + `parser` (required), optional `timeout` in
seconds (a timeout yields `rc=124` and a non-green status). Any other key is
passed to the parser as an option (`fail_under`, `score_fail_under`,
`fail_levels`, …).

**Alias**: `members = ["atomic", …]` only (optional `fail_fast`). No nesting.
See [Aliases & aggregates](aliases.md).

Commands are tokenized with `shlex` and run **without a shell** — no pipes, no
redirects, no `&&`. Deliberate: a shell pipeline is exactly where exit codes
get laundered (`cmd | tee` reports tee's status). If a check needs shell
features, wrap them in a script and point `command` at it. `{run_dir}` is
substituted in commands and artifact paths — point machine-readable reports
into the run directory.

## Command policy

Default `workspace`: before any subprocess starts, path-like argv tokens must
resolve inside the invocation `cwd` (`--cwd` / `CKDN_CWD`). `/etc/passwd`,
`..` escapes, and paths under `/etc`, `/proc`, `~/.ssh`, etc. are rejected.
MCP `extra_args` are subject to the same rules.

- Set `command_policy = "allowlist"` to require configured command prefixes
  (`uv run `, `uvx `, …, or custom `[run.command_allowlist].prefixes`).
- Use `command_policy = "off"` only for exotic workflows.

In CI, `ckdn lock-config` then `ckdn verify-config --locked` catches tampered
commands without running them.

## Pre-flight diagnostics

`ckdn doctor` runs static, deterministic checks over `ckdn.toml` **before** any
subprocess, so a misconfiguration surfaces as an actionable message instead of
a confusing runtime `error` ("report not found"). It reports two levels:

- **error** — a run that cannot possibly work: the command's executable is not
  on `PATH`, or the command is empty / not tokenizable.
- **warning** — a likely mismatch between a command and its parser: a
  file-based parser (`pytest`, `coverage`, `ruff`, `bandit`, `pip_audit`,
  `pylint`, `sarif`) whose command never writes the report it will read, or a
  flag a parser expects (`mypy --output json`, `pyright --outputjson`,
  `reformat --check`).

```console
$ ckdn doctor
error: [ghost] executable not found on PATH: totally-not-installed
warning: [pytest] the pytest parser reads `junit.xml` from the run dir, but
the command never writes it — add the flag that emits `{run_dir}/junit.xml`
1 error(s), 1 warning(s)
```

Exit code is `1` on any error (or on warnings too with `--strict`), else `0` —
so it drops into CI as a config gate. Diagnostics are advisory heuristics; they
never run a command and are separate from the [status model](status-model.md).

## Working directory

Subprocesses and relative `.agent-runs/` resolve from **cwd**, not from where
`ckdn.toml` lives.

- **CLI:** `--cwd` / `CKDN_CWD`.
- **MCP:** per-call `cwd` on every config-using tool, or `CKDN_CWD` /
  `ckdn-mcp --cwd` as server defaults.

When the config file is outside the project tree (worktree, temp config), pass
the project root as cwd on every run — otherwise tools execute in the wrong
directory.
