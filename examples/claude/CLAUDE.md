<!--
SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
SPDX-License-Identifier: MIT
-->

# ckdn verification boundary

Run project checks **only** through ckdn (`ckdn run <check>` or MCP
`run_check` / `run_group`). Read `ckdn.digest/2` from stdout or tool
results — `pass` is the only green state.

- Never run pytest, ruff, ty, pre-commit, or other configured tools
  directly when a ckdn check exists for them.
- Never read `full.log` whole; use digest `findings`, `log_tail`, and
  bounded `get_evidence` / `ckdn show --evidence`.
- Never edit `.agent-runs/` or weaken `ckdn.toml` to go green.

**Working directory:** subprocesses and `.agent-runs/` anchor on cwd, not
the config file parent. When `ckdn.toml` lives outside the project tree
(worktree, Glass slice), pass `--cwd` (CLI) or `cwd` (MCP) as the project
root on every call.

Copy `examples/claude/skills/verified-fix-loop/SKILL.md` into the agent
skills directory for the full fix-loop procedure.
