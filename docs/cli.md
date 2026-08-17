---
icon: lucide/terminal
---

# CLI

`ckdn --version` prints the version. `python -m ckdn` is equivalent to the
`ckdn` entry point.

Global flags: `--config PATH`, `--cwd DIR` (working directory for subprocesses
and relative `runs_dir`; else `CKDN_CWD`). Every command accepts them except
`schema`, which reads no config. `ckdn init` accepts them too, and resolves its
target through the same function the reading commands use — so the command that
*writes* the config and the ones that *read* it cannot disagree about where it
lives.

| Command                                            | Purpose                                                              |
|----------------------------------------------------|----------------------------------------------------------------------|
| `ckdn run <atomic> [--quiet] [-- extra…]`          | run one check; compact digest on stdout                              |
| `ckdn run <alias> [--fail-fast] [--quiet]`         | run its members in config order → aggregate on stdout                |
| `ckdn run --all [--fail-fast] [--quiet]`           | run every atomic check in config order → aggregate on stdout         |
| `ckdn run … --gate`                                | exit reflects the [baseline](baselines.md) gate, not execution       |
| `ckdn baseline <check>`                            | record a check's current findings as the accepted baseline           |
| `ckdn show [run-dir]`                              | pretty-print a stored digest (latest default)                        |
| `ckdn list [-n N] [--json]`                        | recent runs (text, or `{"runs": […]}` with `--json`)                 |
| `ckdn checks [--json]`                             | configured checks (text, or `{"checks": […]}` with `--json`)         |
| `ckdn gc [--keep N]`                               | prune old run directories                                            |
| `ckdn init`                                        | write starter `ckdn.toml` at the resolved config path                |
| `ckdn schema [id]`                                 | print a packaged JSON Schema, or list schema ids                     |
| `ckdn doctor [--strict]`                           | pre-flight diagnostics (executables on PATH + parser/command fit)    |
| `ckdn annotate [ref] [--format F]`                 | render a stored digest's findings as `github` annotations or `sarif` |
| `ckdn verify-config [--locked] [--lock-file PATH]` | validate command policy (+ optional `ckdn.lock.toml`)                |
| `ckdn lock-config [-o path]`                       | write command SHA-256 lock file for CI                               |

`--gate` works on an alias and on `--all` too: the aggregate gate combines the
members', worst-first — `unavailable` > `fail` > `pass`.

`ckdn baseline` needs `baseline = "…"` under `[run]`; without it there is
nowhere to record, and it exits 2 rather than inventing a path.

`-- extra…` is for atomic checks only: an alias rejects extra arguments (exit
2), listing its members so you can run the one you meant. `--fail-fast` is the
mirror image — it stops a *sequence*, so it applies to an alias or to `--all`,
overrides an alias's configured `fail_fast`, and is rejected on a single
atomic check (exit 2: one command is not a sequence). Only the flag's presence
overrides; there is no `--no-fail-fast`.

Alias stdout is **only** the aggregate — and stdout is the only copy of it.
Run directories are written per member, so the aggregate is never stored and
`latest` ends up pointing at the **last member**: after `ckdn run <alias>` or
`ckdn run --all`, `ckdn show` prints that member's digest, not the aggregate.
Redirect stdout if you need the aggregate afterwards.

`list` and `checks` default to human-readable tab-separated text; add `--json`
for machine consumption (same `{"runs": […]}` / `{"checks": […]}` shape the
MCP `list_runs` / `list_checks` tools return).

## Exit codes

`ckdn run` reports the check: the command's own nonzero code passes through,
and `rc == 0` with a non-green digest becomes `1` — ckdn may downgrade green,
never upgrade red. A code outside 1–255 (a signal death, e.g. `-9`) becomes
`1`; it is not saturated to the nearest bound. The full contract, including
the synthetic `124` / `126` / `127` / `130`, is in the
[status model](status-model.md#exit-code-contract).

**`2` means ckdn refused to start** — nothing ran, so there is no verdict.
Every subcommand reserves it, which is what lets CI tell a broken invocation
from a red check instead of seeing both as "nonzero". Exit 2 covers:

- usage errors (unknown flag, missing subcommand);
- config errors — no `ckdn.toml`, invalid TOML, a mistyped value;
- an unknown check name, or an unknown run id for `show` / `annotate`;
- `-- extra` passed to an alias or to `--all`;
- `--fail-fast` on a single atomic check;
- a run-lock conflict — that check is already running in this workspace;
- `ckdn baseline` with no `baseline = "…"` under `[run]`;
- `ckdn init` when the target already exists, or its parent directory does not.

`doctor` and `verify-config` exit `1` in the opposite situation: they ran, and
found problems. That is a verdict, not a refusal.

## CI annotations

`ckdn annotate` projects a stored digest's findings onto a CI surface without
running anything or changing the run's status:

```bash
ckdn run pytest || { rc=$?; ckdn annotate; exit $rc; }   # inline ::error, still red
ckdn annotate --format sarif > ckdn.sarif                # upload to code scanning
```

The braces are not decoration. `annotate` always exits `0` — it is a
projection, not a verdict — and a CI step takes the code of its last command.
Piping the run into a bare `|| ckdn annotate` therefore ends the step on that
`0` and reports a failing run as green: the exact false green ckdn exists to
prevent. Capture the run's code and re-raise it.

- `--format github` (default) emits GitHub Actions workflow commands
  (`::error file=…,line=…::message`), one per finding, so failures show inline
  on the pull request.
- `--format sarif` emits a minimal SARIF 2.1.0 document (tool driver `ckdn`,
  one rule per finding `kind`) for a code-scanning dashboard.

`annotate` renders one stored digest. Since an alias stores no aggregate, the
default `latest` after `ckdn run <alias>` or `ckdn run --all` is the last
member — every other member's findings are silently absent. Pass the run id of
each member you want annotated.
