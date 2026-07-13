# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Tests for shared MCP agent guidance copy."""

from __future__ import annotations

from ckdn.mcp.guidance import CWD_TOOL_HINT, MCP_SERVER_INSTRUCTIONS


def test_mcp_guidance_mentions_cwd_resolution() -> None:
    assert "CKDN_CWD" in MCP_SERVER_INSTRUCTIONS
    assert "cwd" in CWD_TOOL_HINT.lower()
    assert "runs_dir" in CWD_TOOL_HINT.lower()
