# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Tests for shared MCP agent guidance copy."""

from __future__ import annotations

from pathlib import Path

import pytest

from ckdn.mcp.context import ServerContext
from ckdn.mcp.guidance import CWD_TOOL_HINT, MCP_SERVER_INSTRUCTIONS

ARROW = "→"

# Label -> mechanism, deliberately unordered: this test derives the ordering
# from ``resolve_cwd`` itself, so nothing here may encode a precedence claim.
PER_CALL = "per-call cwd"
SERVER_FLAG = "ckdn-mcp --cwd"
ENV_VAR = "CKDN_CWD"
PROCESS_CWD = "process cwd"


def _advertised_chain(text: str) -> tuple[str, ...]:
    """Pull the single ``a -> b -> c`` chain out of agent-facing copy.

    The chain is bounded by ordinary sentence punctuation, so the surrounding
    prose can be rewritten freely without touching this parser.
    """
    normalized = " ".join(text.split())
    first = normalized.index(ARROW)
    last = normalized.rindex(ARROW)
    start = max(normalized.rfind(delim, 0, first) for delim in ".:(") + 1
    ends = [normalized.find(delim, last) for delim in ".)"]
    end = min(index for index in ends if index != -1)
    return tuple(part.strip() for part in normalized[start:end].split(ARROW))


def _observed_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[str, ...]:
    """Rank the cwd sources by running ``resolve_cwd``, never by assumption.

    Every source is enabled with a distinct marker directory; whichever marker
    comes back won this round, so it is recorded and switched off, and the next
    round reveals the runner-up. ``process cwd`` is the implicit floor: when
    ``resolve_cwd`` returns ``None`` the subprocess inherits the server's own
    working directory.
    """
    markers = {
        PER_CALL: tmp_path / "per_call",
        SERVER_FLAG: tmp_path / "server_flag",
        ENV_VAR: tmp_path / "env_var",
    }
    for marker in markers.values():
        marker.mkdir()
    winners: dict[Path | None, str] = {
        marker.resolve(): label for label, marker in markers.items()
    }
    winners[None] = PROCESS_CWD

    active = {*markers, PROCESS_CWD}
    observed: list[str] = []
    while active:
        if ENV_VAR in active:
            monkeypatch.setenv(ENV_VAR, str(markers[ENV_VAR]))
        else:
            monkeypatch.delenv(ENV_VAR, raising=False)
        ctx = ServerContext(
            default_cwd=markers[SERVER_FLAG].resolve()
            if SERVER_FLAG in active
            else None
        )
        call_cwd = str(markers[PER_CALL]) if PER_CALL in active else None
        winner = winners[ctx.resolve_cwd(call_cwd)]
        assert winner in active, (
            f"resolve_cwd returned the disabled source {winner!r}; "
            f"still enabled: {sorted(active)}"
        )
        observed.append(winner)
        active.remove(winner)
    return tuple(observed)


def test_mcp_guidance_mentions_cwd_resolution() -> None:
    assert ENV_VAR in MCP_SERVER_INSTRUCTIONS
    assert "cwd" in CWD_TOOL_HINT.lower()
    assert "runs_dir" in CWD_TOOL_HINT.lower()


def test_advertised_cwd_order_matches_resolve_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The published contract must be the observed behaviour, not a copy of it.

    ``MCP_SERVER_INSTRUCTIONS`` is shipped to every MCP client as the server's
    ``instructions`` field and ``CWD_TOOL_HINT`` is appended to all six tool
    descriptions, so an agent picks its cwd argument from this text. The
    expectation below is produced by executing ``ServerContext.resolve_cwd``,
    which is why reordering the resolver breaks this test until the copy is
    updated to match -- and vice versa.
    """
    observed = _observed_precedence(monkeypatch, tmp_path)
    assert _advertised_chain(MCP_SERVER_INSTRUCTIONS) == observed
    assert _advertised_chain(CWD_TOOL_HINT) == observed
