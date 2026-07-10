# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""FastMCP stdio server entry for ckdn."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from ckdn import __version__
from ckdn.mcp.context import ServerContext
from ckdn.mcp.register import register_all


def _import_fastmcp() -> Any:
    try:
        from fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised when extra missing
        raise SystemExit(
            "ckdn MCP requires the optional extra: pip install 'ckdn[mcp]' "
            "(or: uv add --dev 'ckdn[mcp]')"
        ) from exc
    return FastMCP


def create_server(*, config: Path | None = None) -> Any:
    """Build a FastMCP server with all ckdn tools registered."""
    fastmcp_cls = _import_fastmcp()
    mcp = fastmcp_cls(
        name="ckdn",
        instructions=(
            "ckdn is a deterministic verification boundary between coding agents "
            "and developer tools. Prefer run_check/run_group digests over raw logs. "
            "fail/error/parse_mismatch are trusted structured results — not tool "
            "failures. Use get_evidence only when you need bounded artifact slices. "
            "Never invent shell commands; only configured ckdn.toml checks."
        ),
        version=__version__,
    )
    ctx = ServerContext(default_config=config)
    register_all(mcp, ctx)
    return mcp


def main(argv: list[str] | None = None) -> None:
    """stdio entry point for ``ckdn-mcp``."""
    parser = argparse.ArgumentParser(prog="ckdn-mcp", description="ckdn MCP server")
    parser.add_argument(
        "--config",
        help="default path to ckdn.toml (else CKDN_CONFIG or ./ckdn.toml)",
    )
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    config = Path(args.config) if args.config else None
    create_server(config=config).run()


if __name__ == "__main__":
    main()
