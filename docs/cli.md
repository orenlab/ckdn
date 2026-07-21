---
icon: lucide/terminal
---

# CLI

Global flags (on commands that load config): `--config PATH`, `--cwd DIR`
(working directory for subprocesses and relative `runs_dir`; else `CKDN_CWD`).

| Command                                  | Purpose                                                         |
|------------------------------------------|-----------------------------------------------------------------|
| `ckdn run <check> [--quiet] [-- extra…]` | run atomic check or alias; compact digest / aggregate on stdout |
| `ckdn show [run-dir]`                    | pretty-print a stored digest (latest default)                   |
| `ckdn list [-n N] [--json]`              | recent runs (text, or `{"runs": […]}` with `--json`)            |
| `ckdn checks [--json]`                   | configured checks (text, or `{"checks": […]}` with `--json`)    |
| `ckdn gc [--keep N]`                     | prune old run directories                                       |
| `ckdn init`                              | write starter `ckdn.toml`                                       |
| `ckdn schema [id]`                       | print a packaged JSON Schema, or list schema ids                |
| `ckdn doctor [--strict]`                 | pre-flight diagnostics (executables on PATH + parser/command fit) |
| `ckdn verify-config [--locked]`          | validate command policy (+ optional `ckdn.lock.toml`)           |
| `ckdn lock-config [-o path]`             | write command SHA-256 lock file for CI                          |

Alias stdout is **only** the aggregate; member digests stay under
`.agent-runs/` for `ckdn show`.

`list` and `checks` default to human-readable tab-separated text; add `--json`
for machine consumption (same `{"runs": […]}` / `{"checks": […]}` shape the
MCP `list_runs` / `list_checks` tools return).
