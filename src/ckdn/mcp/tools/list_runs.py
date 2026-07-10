# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""list_runs MCP tool."""

from __future__ import annotations

from typing import Any

from ckdn.app import list_runs as app_list_runs
from ckdn.mcp.context import ServerContext


def register(mcp: Any, ctx: ServerContext) -> None:
    @mcp.tool(  # type: ignore[untyped-decorator]
        name="list_runs",
        description="List recent ckdn run directories with check/status summaries.",
    )
    def list_runs(limit: int = 10, config: str | None = None) -> dict[str, Any]:
        """Return recent runs.

        Args:
            limit: Max number of runs (most recent window).
            config: Optional path to ckdn.toml.
        """
        cfg = ctx.load(config)
        return {"runs": app_list_runs(cfg, limit=limit)}
