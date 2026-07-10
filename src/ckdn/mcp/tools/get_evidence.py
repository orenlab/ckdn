# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""get_evidence MCP tool."""

from __future__ import annotations

from typing import Any

from ckdn.app import DEFAULT_EVIDENCE_LIMIT
from ckdn.app import get_evidence as app_get_evidence
from ckdn.mcp.context import ServerContext


def register(mcp: Any, ctx: ServerContext) -> None:
    @mcp.tool(  # type: ignore[untyped-decorator]
        name="get_evidence",
        description=(
            "Read bounded evidence for a run. Without artifact: digest findings/"
            "gates/notes/log_tail + artifact index (never full.log body). "
            "With artifact: line-sliced file contents (default 200 lines, max 2000)."
        ),
    )
    def get_evidence(
        run: str | None = None,
        artifact: str | None = None,
        offset: int = 0,
        limit: int = DEFAULT_EVIDENCE_LIMIT,
        include_meta: bool = False,
        config: str | None = None,
    ) -> dict[str, Any]:
        """Return bounded evidence.

        Args:
            run: Run directory name; omit for latest.
            artifact: Optional artifact filename inside the run dir (e.g. full.log).
            offset: Line offset into the artifact.
            limit: Max lines to return (capped at 2000).
            include_meta: Include meta.json when present.
            config: Optional path to ckdn.toml.
        """
        cfg = ctx.load(config)
        return app_get_evidence(
            cfg,
            ref=run,
            artifact=artifact,
            offset=offset,
            limit=limit,
            include_meta=include_meta,
        )
