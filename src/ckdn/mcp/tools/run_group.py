# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""run_group MCP tool (aliases only)."""

from __future__ import annotations

from typing import Any

from ckdn.app import NotAliasError, run_alias
from ckdn.app.errors import UnknownCheckError
from ckdn.mcp.context import ServerContext


def register(mcp: Any, ctx: ServerContext) -> None:
    @mcp.tool(  # type: ignore[untyped-decorator]
        name="run_group",
        description=(
            "Run an alias (members) from ckdn.toml and return "
            "{aggregate, exit_code}. Does not accept extra_args. "
            "Member fail/error statuses are normal results, not tool errors."
        ),
    )
    def run_group(alias: str, config: str | None = None) -> dict[str, Any]:
        """Run an alias group.

        Args:
            alias: Alias check name from ckdn.toml.
            config: Optional path to ckdn.toml.
        """
        cfg = ctx.load(config)
        item = cfg.checks.get(alias)
        if item is None:
            raise UnknownCheckError(
                f"unknown check '{alias}'; configured: " + ", ".join(sorted(cfg.checks))
            )
        if not item.is_alias:
            raise NotAliasError(f"'{alias}' is atomic; use run_check")
        result = run_alias(cfg, item)
        return {"aggregate": result.aggregate, "exit_code": result.exit_code}
