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

[check.format]
command = "uv run ruff format --check ."
parser = "reformat"

[check.lint]
members = ["ruff"]               # add pylint / bandit / … when enabled

[check.style]
members = ["format", "ruff"]     # runs format first, then ruff
```

Every member an alias names must exist. A `members` entry that no
`[check.<name>]` defines is rejected when the file loads, so the mistake takes
down *every* command — not just the alias that made it.

## Run settings

`[run]` keys, with their defaults:

- `runs_dir = ".agent-runs"` — where run artifacts live. Relative paths
  resolve from cwd.
- `keep = 20` — how many *finished* run directories `runs_dir` retains, across
  all checks. An unfinished run is never pruned, so a fast check cannot delete
  a slow one's directory out from under it.
- `top = 20` — max findings listed in a digest; a check may override it.
- `max_snippet_lines = 12` — max detail lines per finding.
- `log_tail_lines = 40` — how many log lines a digest carries when it includes
  a tail.
- `command_policy = "workspace"` — see [Command policy](#command-policy).
- `baseline` — unset. Path to the baseline file; like `runs_dir`, a relative
  path resolves from **cwd**, not from the config file's parent. See
  [Baselines](baselines.md).

The four integers (`keep`, `top`, `max_snippet_lines`, `log_tail_lines`) are
strict: a string, a float or a bool is a config error (exit 2), never a silent
coercion. `keep = 2.5` is a typo, not a request for two — the number you wrote
is not the number you meant, and ckdn says so instead of guessing. The same
holds elsewhere — `timeout` takes an integer or a float **greater than zero**,
and `fail_fast` must be a real TOML boolean, so `fail_fast = "false"` is
rejected rather than read as the truthy string it is.

## Checks

**Atomic** check: `command` + `parser` (required), optional `timeout` in
seconds — a TOML number greater than zero, never a string. `timeout = 0` is
refused rather than read as "no limit": a deadline that has already passed
would kill the check the moment it starts. To run without a limit, leave
`timeout` out entirely. A timeout yields `rc=124` and status `error`
specifically, never `fail`: the tool was killed mid-flight, and partial
evidence is not a verdict.

Most other keys are passed to the parser as options (`fail_under`,
`score_fail_under`, `fail_levels`, …); `fail_fast` is rejected outright on an
atomic check. Two more are not just parser options:

- **`env`** is reserved and never reaches the parser (see below).
- **`top`** does reach the parser, but ckdn reads it too: it overrides
  `[run].top` for this check's digest only.

!!! warning "Set a `timeout` on long checks"

    `timeout` is optional and unset means *wait indefinitely*. A tool that
    hangs then hangs the check. ckdn will not leak the process tree — it is
    terminated on interrupt — but only a `timeout` bounds an unattended run,
    so set one on anything that talks to the network or runs a full suite.

Runs are serialized per `(runs_dir, check)`: starting a second `ckdn run` of
the same check in the same workspace is refused while the first is alive,
rather than doubling the load on the same tools. A lock left by a dead process
is reclaimed automatically, and the run that reclaims it records a note saying
the previous run did not exit cleanly and may have left processes behind.

That note is advisory: it describes the *previous* run, so it never changes
this run's status. ckdn does not stop leftover processes for you. Only its own
pid is ever recorded — never the child's process group — and a pid can be
recycled, so there is no target it could act on without risking an unrelated
process. If a run behaves oddly right after such a note, look for leftovers
from the previous one.

Optional **`env`** (a table of string values) is overlaid on the subprocess
environment for that check only — the inherited environment (`PATH`, …) is
preserved. `{run_dir}` is substituted in env values, so a tool can be pointed
at the run directory. `env` is never written to `meta.json`.

```toml
[check.coverage]
command = "pytest --cov=src --cov-report=xml:{run_dir}/coverage.xml"
parser = "coverage"
env = { COVERAGE_FILE = "{run_dir}/.coverage", PYTHONWARNINGS = "error" }
```

**Alias**: `members = ["atomic", …]` only (optional `fail_fast`). Membership is
validated when the file loads, with four distinct errors: a member no
`[check.<name>]` defines, a member that is itself an alias (nesting is
unsupported — which is why a cycle cannot be expressed at all), an alias
listing itself, and a member listed twice. The duplicate is a hard error, not
a silent de-duplication: it is always a mistake, and running the same tool
twice in one sequence is never what was meant. See
[Aliases & aggregates](aliases.md).

Commands are tokenized with `shlex` and run **without a shell** — no pipes, no
redirects, no `&&`. Deliberate: a shell pipeline is exactly where exit codes
get laundered (`cmd | tee` reports tee's status). If a check needs shell
features, wrap them in a script and point `command` at it. `{run_dir}` is
substituted in commands and artifact paths — point machine-readable reports
into the run directory.

## Command policy

Default `workspace`: before any subprocess starts, path-like argv tokens must
resolve inside the invocation `cwd` (`--cwd` / `CKDN_CWD`). `..` escapes and
absolute paths outside cwd are rejected by that containment check. A second,
narrower denylist — `/etc`, `/proc`, `/sys`, `/dev` and `~/.ssh`, `~/.aws`,
`~/.gnupg`, `~/.netrc`, `~/.docker`, `~/.kube` — applies to paths that pass
containment, so it only bites when cwd is itself inside one of them. MCP
`extra_args` are subject to the same rules.

The three settings are not a spectrum from loose to strict:

- `allowlist` **narrows** `workspace`. Containment still applies in full, and
  the configured command must *additionally* match an allowed prefix. An
  approved executable is still not allowed to read outside the workspace.
- `off` is the only setting that widens, and it drops both conditions. Use it
  only when you accept full subprocess scope.

The built-in prefix set is exactly `uv run `, `uvx `, `true` and `false`. The
last two carry no trailing space, so they match on a word boundary — the bare
command, or the command followed by a space and arguments: `truex` is rejected,
`true --wat` is not. Custom `[run.command_allowlist].prefixes` **replace** that
set instead of extending it, so list every prefix you still need. Uncommenting
the starter's `prefixes = ["make ", "./scripts/"]` blocks every check the
starter ships: seven begin `uv run `, and `[check.lock]` (`uv lock --check`)
matches neither prefix. Note that `uv lock --check` is outside the built-in set
too, so `allowlist` blocks it even with no custom `prefixes`.

A rejected check is not an exception and does not abort the run. It gets a
normal run directory and digest, an empty `full.log`, `rc` 126 and status
`error`, and a note naming the prefixes that were allowed. No subprocess is
ever started.

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

- **CLI:** `--cwd` → `CKDN_CWD` → process cwd.
- **MCP:** per-call `cwd` (accepted by every config-using tool) →
  `ckdn-mcp --cwd` → `CKDN_CWD` → process cwd. The server's own `--cwd`
  outranks the environment, so an explicitly launched server is not silently
  redirected by a stray variable.

When the config file is outside the project tree (worktree, temp config), pass
the project root as cwd on every run — otherwise tools execute in the wrong
directory.
