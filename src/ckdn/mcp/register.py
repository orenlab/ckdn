# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Factory: register all MCP tools on a FastMCP instance."""

from __future__ import annotations

from typing import Any

from ckdn.mcp import tools as tool_package
from ckdn.mcp.context import ServerContext
from ckdn.mcp.tools import (
    get_digest,
    get_evidence,
    list_checks,
    list_runs,
    run_check,
    run_group,
)

_TOOL_MODULES = (
    list_checks,
    run_check,
    run_group,
    get_digest,
    list_runs,
    get_evidence,
)


def register_all(mcp: Any, ctx: ServerContext) -> None:
    """Attach every tool module to ``mcp``."""
    for module in _TOOL_MODULES:
        module.register(mcp, ctx)


# Keep package import referenced for discoverability / linters.
_ = tool_package
