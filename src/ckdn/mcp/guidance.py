# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Shared agent-facing copy for MCP tools and server instructions."""

from __future__ import annotations

MCP_SERVER_INSTRUCTIONS = (
    "ckdn is a deterministic verification boundary between coding agents "
    "and developer tools. Prefer run_check/run_group digests over raw logs. "
    "fail/error/parse_mismatch are trusted structured results — not tool "
    "failures. Use get_evidence only when you need bounded artifact slices. "
    "Never invent shell commands; only configured ckdn.toml checks. "
    "Working directory: every config-using tool accepts optional cwd. "
    "Resolution order: per-call cwd → CKDN_CWD → ckdn-mcp --cwd → process "
    "cwd. Subprocesses and relative runs_dir (.agent-runs) anchor on cwd, "
    "not the config file parent. When ckdn.toml lives outside the project "
    "tree (worktree, temp config, Glass slice), pass cwd as the project "
    "root on every tool call; config may point elsewhere. "
    "list_checks discovers atomic checks and aliases (e.g. hooks, pre_commit). "
    "Governance (lock-config, verify-config) is CLI/CI only — not MCP tools."
)

CWD_TOOL_HINT = (
    "Optional cwd: subprocess working directory and anchor for relative "
    "runs_dir (per-call cwd → CKDN_CWD → ckdn-mcp --cwd → process cwd). "
    "When config is outside the project, pass the project root as cwd."
)
