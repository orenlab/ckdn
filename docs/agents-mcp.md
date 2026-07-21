---
icon: lucide/bot
---

# Agents & MCP

## Agent integration

Four layers, increasing strength:

1. **Standing rule** (`CLAUDE.md` / equivalent) — run only via
   `ckdn run <check>` or MCP `run_check` / `run_group`; read the digest;
   `pass` is the only green; never edit `.agent-runs/` or weaken checks to go
   green. Template: `examples/claude/CLAUDE.md`.
2. **Skill** — `examples/claude/skills/verified-fix-loop/SKILL.md` (copy into
   the agent's skills dir). Bounded fix loop, digest-only reading, forbidden
   moves, MCP tool mapping, and `cwd` for worktrees.
3. **Hooks / CI** — `ckdn run` passes red exit codes through, so it drops into
   the same slots as the raw tool, with digests as a side effect. Use
   `ckdn lock-config` + `ckdn verify-config --locked` in CI for command
   governance (not exposed as MCP tools).
4. **MCP** (optional) — `ckdn[mcp]` / `ckdn-mcp` when the client should call
   ckdn over the protocol instead of shelling out (see below).

Division of labor: constitution → procedure → instrumentation → enforcement.
Digests never contain instructions to the agent (prompt-injection surface and
policy fork).

## MCP

When an agent should call ckdn over MCP instead of shelling out, install the
FastMCP transport:

```bash
uv tool install 'ckdn[mcp]'
```

`ckdn-mcp` speaks **stdio** only. Config resolution: `--config` →
`$CKDN_CONFIG` → `./ckdn.toml` (process cwd). Working directory: `--cwd` →
`$CKDN_CWD` → process cwd. **Subprocesses and relative `runs_dir` anchor on
cwd, not the config file parent** — pass `cwd` on every tool call when config
and project root differ.

Every client shares the schema `{ command, args, env }`; only the file name
and format differ.

=== "Claude Code"

    Project-scoped `.mcp.json` (committed):

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
            "CKDN_CONFIG": "${CKDN_CONFIG:-ckdn.toml}",
            "CKDN_CWD": "${CKDN_CWD:-}"
          }
        }
      }
    }
    ```

    For worktree slices, prefer per-call `cwd` on each tool instead of a fixed
    env default.

=== "Cursor"

    `.cursor/mcp.json` (or global `~/.cursor/mcp.json`):

    ```json
    {
      "mcpServers": {
        "ckdn": {
          "command": "ckdn-mcp",
          "args": [],
          "env": {
            "CKDN_CONFIG": "/absolute/path/to/ckdn.toml",
            "CKDN_CWD": "/absolute/path/to/project-root"
          }
        }
      }
    }
    ```

=== "Claude Desktop"

    Settings → Developer → Edit Config, same schema as Cursor
    (`claude_desktop_config.json`).

=== "ChatGPT Codex"

    `~/.codex/config.toml` (TOML, not JSON):

    ```toml
    [mcp_servers.ckdn]
    command = "ckdn-mcp"
    args = []
    env = { CKDN_CONFIG = "/absolute/path/to/ckdn.toml", CKDN_CWD = "/absolute/path/to/project-root" }
    ```

=== "Worktree / temp config"

    When `ckdn.toml` lives outside the project tree, pass **project root** as
    `cwd` on every MCP tool (same as CLI `--cwd`):

    ```json
    {
      "check": "tests",
      "config": "/tmp/ckdn.toml",
      "cwd": "/path/to/worktree"
    }
    ```

### Tools

Thin adapter over the same application layer as the CLI. All config-using tools
accept optional `config` and `cwd`:

| Tool           | Purpose                                                               |
|----------------|-----------------------------------------------------------------------|
| `list_checks`  | Configured atomic checks + aliases                                    |
| `run_check`    | Run one **atomic** check → `{digest, exit_code}`                      |
| `run_group`    | Run one **alias** → `{aggregate, exit_code}`                          |
| `get_digest`   | Load stored `ckdn.digest/2` (latest or by run id)                     |
| `list_runs`    | Recent run summaries                                                  |
| `get_evidence` | Bounded findings / artifact line slices (never auto-dumps `full.log`) |

### Trust rules

- Only checks from `ckdn.toml` — no arbitrary shell.
- `fail` / `error` / `parse_mismatch` are **normal structured results**, not
  MCP tool failures.
- MCP `isError` is reserved for impossible tool calls (missing config, unknown
  check, path escape).
- `run` is a run id (single directory name), never a path; refs that escape
  `.agent-runs/` are `isError`, not silent reads.
- `exit_code` in tool results is a convenience mirror of the digest's `rc`; the
  digest is the source of truth.
- `lock-config` / `verify-config` are CLI/CI governance — not MCP tools.
- Core CLI remains stdlib-only; FastMCP is the optional extra.
