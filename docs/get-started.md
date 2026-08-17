---
icon: lucide/rocket
---

# Get started

## Install

```bash
uv tool install ckdn          # global CLI
# or as a project dev dependency:
uv add --dev ckdn
```

The core has zero dependencies. The MCP transport is an optional extra — see
[Agents & MCP](agents-mcp.md#mcp).

## Quick start

```bash
cd your-project
ckdn init                      # writes starter ckdn.toml
# edit commands / parsers / aliases to match the project
echo '.agent-runs/' >> .gitignore

ckdn checks                    # list configured checks
ckdn doctor                    # pre-flight: tools on PATH, commands fit parsers
ckdn run ruff                  # one atomic check
ckdn run lint                  # alias → its members in order
ckdn run --all                 # every atomic check → one aggregate
ckdn show                      # pretty-print the latest run's digest
ckdn list                      # recent runs
```

The edit step is not optional: the starter config enables checks a given
project may not have (`coverage`, `mypy`, `pre_commit`, …), and `--all`
reports the missing ones as `error` — or `fail`, for a check on the `generic`
parser, which promises no findings to go missing.

`ckdn doctor` is worth the one run after `ckdn init`: it reads the config
without starting anything and says which commands cannot work as written. See
[Pre-flight diagnostics](configuration.md#pre-flight-diagnostics).

An alias and `--all` print their aggregate to stdout only — it is never stored,
and `latest` points at the last member that ran, so the `ckdn show` above
prints that member's digest rather than the aggregate. See [CLI](cli.md).

Each run writes a directory under `.agent-runs/` holding the full log, tool
artifacts, provenance (`meta.json`), and the deterministic `digest.json`. The
digest is the only thing an agent should read; see
[Digests & schemas](digests.md).

!!! tip "One command, drop-in"

    `ckdn run` passes the original command's nonzero exit code through, so it
    slots into any hook or CI step where the raw command used to be — with a
    bounded digest as a side effect. The exception is the one ckdn exists for:
    `rc == 0` with a non-green digest exits `1`. A code outside 1–255 (a signal
    death) becomes `1`, not the nearest bound. See the
    [exit-code contract](status-model.md#exit-code-contract), and
    [exit codes](cli.md#exit-codes) for the `2` that means ckdn refused to run.
