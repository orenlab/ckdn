# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""run_check MCP tool (atomic checks only)."""

from __future__ import annotations

from typing import Any

from ckdn.app import NotAtomicError, run_one
from ckdn.app.errors import UnknownCheckError
from ckdn.mcp.context import ServerContext


def register(mcp: Any, ctx: ServerContext) -> None:
    @mcp.tool(  # type: ignore[untyped-decorator]
        name="run_check",
        description=(
            "Run one atomic check from ckdn.toml and return "
            "{digest: ckdn.digest/2, exit_code}. "
            "fail/error/parse_mismatch are normal results, not tool errors. "
            "Aliases must use run_group. Never returns full.log."
        ),
    )
    def run_check(
        check: str,
        extra_args: list[str] | None = None,
        config: str | None = None,
    ) -> dict[str, Any]:
        """Run an atomic check.

        Args:
            check: Atomic check name from ckdn.toml.
            extra_args: Extra argv appended to the configured command.
            config: Optional path to ckdn.toml.
        """
        cfg = ctx.load(config)
        item = cfg.checks.get(check)
        if item is None:
            raise UnknownCheckError(
                f"unknown check '{check}'; configured: " + ", ".join(sorted(cfg.checks))
            )
        if item.is_alias:
            raise NotAtomicError(
                f"'{check}' is an alias; use run_group "
                f"(members: {', '.join(item.members or ())})"
            )
        result = run_one(cfg, item, extra=list(extra_args or ()))
        return {"digest": result.digest, "exit_code": result.exit_code}
