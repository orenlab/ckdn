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
ckdn run ruff                  # one atomic check
ckdn run lint                  # alias → members (e.g. ruff, pylint)
ckdn show                      # pretty-print latest digest
ckdn list                      # recent runs
```

Each run writes a directory under `.agent-runs/` holding the full log, tool
artifacts, provenance (`meta.json`), and the deterministic `digest.json`. The
digest is the only thing an agent should read; see
[Digests & schemas](digests.md).

!!! tip "One command, drop-in"

    `ckdn run` exits with the original command's code, so it slots into any
    hook or CI step where the raw command used to be — with a bounded digest as
    a side effect. See the [exit-code contract](status-model.md#exit-code-contract).
