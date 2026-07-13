# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""list_checks MCP tool."""

from __future__ import annotations

from typing import Any

from ckdn.app import list_checks as app_list_checks
from ckdn.mcp.context import ServerContext
from ckdn.mcp.guidance import CWD_TOOL_HINT


def register(mcp: Any, ctx: ServerContext) -> None:
    @mcp.tool(  # type: ignore[untyped-decorator]
        name="list_checks",
        description=(
            "List atomic checks and aliases from ckdn.toml. Does not run anything. "
            f"{CWD_TOOL_HINT}"
        ),
    )
    def list_checks(
        config: str | None = None, cwd: str | None = None
    ) -> dict[str, Any]:
        """Return configured checks.

        Args:
            config: Optional path to ckdn.toml (else CKDN_CONFIG or ./ckdn.toml).
            cwd: Optional working directory for resolving relative runs_dir.
        """
        cfg = ctx.load(config, cwd)
        return {"checks": app_list_checks(cfg)}
