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
   governance, and `ckdn doctor` for static pre-flight (executables on PATH,
   parser/command fit, no subprocess). None of the three is an MCP tool.
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

`ckdn-mcp` speaks **stdio** only. Config resolution: per-call `config` →
`ckdn-mcp --config` → `$CKDN_CONFIG` → `<resolved cwd>/ckdn.toml`. Working
directory: per-call `cwd` → `ckdn-mcp --cwd` → `$CKDN_CWD` → process cwd. The
server default outranks the environment on both axes, and the implicit config
path follows the *resolved* cwd — it is the process cwd only when nothing
overrides cwd. **Subprocesses and relative `runs_dir` anchor on cwd, not the
config file parent** — pass `cwd` on every tool call when config and project
root differ.

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

Thin adapter over the same application layer as the CLI. Every tool takes
optional `config` (path to `ckdn.toml`) and `cwd`, both defaulting to `null`;
schemas reject unknown keys.

| Tool           | Purpose                                                               |
|----------------|-----------------------------------------------------------------------|
| `list_checks`  | Configured atomic checks + aliases → `{checks: [...]}`                |
| `run_check`    | Run one **atomic** check → `{digest, exit_code}`                      |
| `run_group`    | Run one **alias** → `{aggregate, exit_code}`                          |
| `get_digest`   | Load stored `ckdn.digest/2` (latest or by run id)                     |
| `list_runs`    | Recent run summaries → `{runs: [...]}`                                |
| `get_evidence` | Bounded findings / artifact line slices (never auto-dumps `full.log`) |

Beyond `config` / `cwd`:

- `list_checks` — nothing else.
- `run_check(check, extra_args=null)` — `check` is required and must name an
  atomic check; an alias is an error pointing at `run_group`. `extra_args` is
  a string array appended to the configured command's argv.
- `run_group(alias)` — `alias` is required and must name an alias; an atomic
  name is an error pointing at `run_check`. No `extra_args` — an alias has no
  single member to append them to; run the atomic member instead.
- `get_digest(run=null)` — `run` omitted means the latest run.
- `list_runs(limit=10)` — most recent window, returned oldest→newest.
- `get_evidence(run=null, artifact=null, offset=0, limit=200,
  include_meta=false)` — see below.

`run` is resolved inside the runs directory, so it is a run id — one directory
name — never a path. Limits are clamped rather than rejected: `list_runs` takes
`limit` to `0..500`, `get_evidence` takes `limit` to `1..2000` and `offset` to
`>= 0`, and the clamped values are what come back.

### `get_evidence` result

Without `artifact`, the payload is the run's identity plus whichever digest
evidence keys the run produced:

```json
{
  "run_id": "20260817T173148Z-mismatch",
  "check": "mismatch",
  "status": "parse_mismatch",
  "rc": 0,
  "run_dir": ".agent-runs/20260817T173148Z-mismatch",
  "artifacts": ["full.log", "meta.json", "ruff.json"]
}
```

`run_id` is the directory name — the value `run` accepts. `run_dir` is the
digest's recorded path to it, relative to cwd. They are not interchangeable.
`artifacts` is the live directory listing, not the digest's copy.

Merged in when present: `findings`, `findings_total`, `findings_truncated`,
`gate_failures`, `notes`, `log_tail`, `summary`, `status_reason`. Sparse as
everywhere else — a missing key means empty / `0` / `false`.

The digest's `gate` and `baseline` blocks are **not** here; read those with
`get_digest`. `include_meta: true` adds `meta` from `meta.json` when it exists
and parses, or `meta_error` when it is corrupt.

Passing `artifact` adds one block and changes nothing else:

```json
{
  "artifact": {
    "name": "full.log",
    "offset": 0,
    "limit": 2,
    "total_lines": 1,
    "truncated": false,
    "lines": ["wrote report, exiting 0"]
  }
}
```

`offset` and `limit` echo the clamped values actually used, and `truncated` is
`offset + limit < total_lines` — so an agent can page without guessing.

### Trust rules

- Only checks from `ckdn.toml` — no arbitrary shell.
- `fail` / `error` / `parse_mismatch` are **normal structured results**, not
  MCP tool failures.
- MCP `isError` is reserved for impossible tool calls (missing config, unknown
  check, path escape).
- `run` is a run id (single directory name), never a path; refs that escape
  `.agent-runs/` are `isError`, not silent reads.
- `exit_code` is **not** a mirror of the digest's `rc`. A nonzero `rc` passes
  through (anything outside 1..255 becomes `1`), but `rc == 0` with a non-green
  status still yields `exit_code: 1` — that is the `parse_mismatch` case and
  the parser-gate `fail` case (e.g. coverage below `fail_under`, where the tool
  itself exits 0). Reading `rc: 0` as green is the exact false green ckdn
  exists to catch; `status` is the verdict. On `run_group` the two do coincide,
  because the aggregate's `rc` *is* that exit code.
- `lock-config` / `verify-config` (governance) and `doctor` (pre-flight) are
  CLI/CI only — not MCP tools.
- Core CLI remains stdlib-only; FastMCP is the optional extra.
