---
icon: lucide/terminal
---

# CLI

Global flags (on commands that load config): `--config PATH`, `--cwd DIR`
(working directory for subprocesses and relative `runs_dir`; else `CKDN_CWD`).

| Command                                  | Purpose                                                         |
|------------------------------------------|-----------------------------------------------------------------|
| `ckdn run <check> [--quiet] [-- extra…]` | run atomic check or alias; compact digest / aggregate on stdout |
| `ckdn run --all [--fail-fast] [--quiet]`  | run every atomic check in config order → aggregate on stdout    |
| `ckdn show [run-dir]`                    | pretty-print a stored digest (latest default)                   |
| `ckdn list [-n N] [--json]`              | recent runs (text, or `{"runs": […]}` with `--json`)            |
| `ckdn checks [--json]`                   | configured checks (text, or `{"checks": […]}` with `--json`)    |
| `ckdn gc [--keep N]`                     | prune old run directories                                       |
| `ckdn init`                              | write starter `ckdn.toml`                                       |
| `ckdn schema [id]`                       | print a packaged JSON Schema, or list schema ids                |
| `ckdn doctor [--strict]`                 | pre-flight diagnostics (executables on PATH + parser/command fit) |
| `ckdn annotate [ref] [--format F]`       | render a stored digest's findings as `github` annotations or `sarif` |
| `ckdn verify-config [--locked]`          | validate command policy (+ optional `ckdn.lock.toml`)           |
| `ckdn lock-config [-o path]`             | write command SHA-256 lock file for CI                          |

Alias stdout is **only** the aggregate; member digests stay under
`.agent-runs/` for `ckdn show`.

`list` and `checks` default to human-readable tab-separated text; add `--json`
for machine consumption (same `{"runs": […]}` / `{"checks": […]}` shape the
MCP `list_runs` / `list_checks` tools return).

## CI annotations

`ckdn annotate` projects a stored digest's findings onto a CI surface without
running anything or changing the run's status:

```bash
ckdn run pytest || ckdn annotate            # inline ::error on the PR
ckdn annotate --format sarif > ckdn.sarif   # upload to code scanning
```

- `--format github` (default) emits GitHub Actions workflow commands
  (`::error file=…,line=…::message`), one per finding, so failures show inline
  on the pull request.
- `--format sarif` emits a minimal SARIF 2.1.0 document (tool driver `ckdn`,
  one rule per finding `kind`) for a code-scanning dashboard.
