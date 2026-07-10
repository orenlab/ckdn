# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Optional FastMCP transport for ckdn (install ``ckdn[mcp]``)."""

from __future__ import annotations

__all__ = ["create_server", "main"]


def __getattr__(name: str) -> object:
    if name in {"create_server", "main"}:
        from ckdn.mcp.server import create_server, main

        return {"create_server": create_server, "main": main}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
