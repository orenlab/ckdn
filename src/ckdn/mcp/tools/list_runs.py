# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""list_runs MCP tool."""

from __future__ import annotations

from typing import Any

from ckdn.app import list_runs as app_list_runs
from ckdn.mcp.context import ServerContext
from ckdn.mcp.guidance import CWD_TOOL_HINT


def register(mcp: Any, ctx: ServerContext) -> None:
    @mcp.tool(  # type: ignore[untyped-decorator]
        name="list_runs",
        description=(
            "List recent ckdn run directories with check/status summaries. "
            f"{CWD_TOOL_HINT}"
        ),
    )
    def list_runs(
        limit: int = 10, config: str | None = None, cwd: str | None = None
    ) -> dict[str, Any]:
        """Return recent runs.

        Args:
            limit: Max number of runs (most recent window).
            config: Optional path to ckdn.toml.
            cwd: Optional working directory for resolving relative runs_dir.
        """
        cfg = ctx.load(config, cwd)
        return {"runs": app_list_runs(cfg, limit=limit)}
