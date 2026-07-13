# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""get_digest MCP tool."""

from __future__ import annotations

from typing import Any

from ckdn.app import get_digest as app_get_digest
from ckdn.mcp.context import ServerContext
from ckdn.mcp.guidance import CWD_TOOL_HINT


def register(mcp: Any, ctx: ServerContext) -> None:
    @mcp.tool(  # type: ignore[untyped-decorator]
        name="get_digest",
        description=(
            "Load a stored ckdn.digest/2 for a run id or the latest run. "
            "Does not re-run checks. "
            f"{CWD_TOOL_HINT}"
        ),
    )
    def get_digest(
        run: str | None = None, config: str | None = None, cwd: str | None = None
    ) -> dict[str, Any]:
        """Return a stored digest.

        Args:
            run: Run directory name; omit for latest.
            config: Optional path to ckdn.toml.
            cwd: Optional working directory for resolving relative runs_dir.
        """
        cfg = ctx.load(config, cwd)
        return app_get_digest(cfg, run)
