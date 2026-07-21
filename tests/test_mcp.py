# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""MCP transport contract tests (requires ``ckdn[mcp]`` / fastmcp)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

# The end-to-end run tests shell out to POSIX ``true``/``false``; the MCP
# adapter itself (errors, cwd, aliases) is covered by the other tests.
posix_run_only = pytest.mark.skipif(
    os.name == "nt", reason="uses POSIX true/false binaries"
)

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from worktree_fixtures import make_worktree_slice  # noqa: E402

from ckdn.mcp.guidance import MCP_SERVER_INSTRUCTIONS  # noqa: E402
from ckdn.mcp.server import create_server  # noqa: E402


def _write_cfg(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "ckdn.toml"
    path.write_text(
        '[run]\nruns_dir = ".agent-runs"\nkeep = 20\n\n' + body,
        encoding="utf-8",
    )
    return path


def _data(result: object) -> Any:
    """Prefer structured `.data`, fall back to `.structured_content`."""
    data = getattr(result, "data", None)
    if data is not None:
        return data
    return getattr(result, "structured_content", None)


def test_mcp_server_instructions_document_cwd_worktree() -> None:
    mcp = create_server()
    instructions = getattr(mcp, "instructions", None) or MCP_SERVER_INSTRUCTIONS
    assert instructions == MCP_SERVER_INSTRUCTIONS
    lowered = instructions.lower()
    assert "cwd" in lowered
    assert "worktree" in lowered or "outside the project" in lowered
    assert "lock-config" in lowered or "verify-config" in lowered


@posix_run_only
@pytest.mark.asyncio
async def test_mcp_per_call_cwd_worktree_slice(tmp_path: Path) -> None:
    """Config outside project: per-call cwd anchors runs_dir and subprocess."""
    slice_ = make_worktree_slice(
        tmp_path,
        body=(
            '[run]\nruns_dir = ".agent-runs"\nkeep = 20\n\n'
            '[check.ok]\ncommand = "true"\nparser = "generic"\n'
        ),
    )
    mcp = create_server()
    async with Client(mcp) as client:
        passed = await client.call_tool(
            "run_check",
            {
                "check": "ok",
                "config": str(slice_.cfg_path),
                "cwd": str(slice_.worktree),
            },
        )
        assert getattr(passed, "is_error", False) is False
        body = _data(passed)
        assert isinstance(body, dict)
        assert body["digest"]["status"] == "pass"
        assert (slice_.worktree / ".agent-runs").is_dir()
        assert not (slice_.config_dir / ".agent-runs").exists()


@posix_run_only
@pytest.mark.asyncio
async def test_mcp_list_and_run_check_pass(tmp_path: Path) -> None:
    cfg_path = _write_cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n'
        '[check.bad]\ncommand = "false"\nparser = "generic"\n'
        '[check.g]\nmembers = ["ok", "bad"]\n',
    )
    mcp = create_server(config=cfg_path, cwd=tmp_path)
    async with Client(mcp) as client:
        listed = await client.call_tool("list_checks", {"config": str(cfg_path)})
        payload = _data(listed)
        assert isinstance(payload, dict)
        names = {c["name"] for c in payload["checks"]}
        assert names >= {"ok", "bad", "g"}

        passed = await client.call_tool(
            "run_check",
            {"check": "ok", "config": str(cfg_path)},
        )
        assert getattr(passed, "is_error", False) is False
        body = _data(passed)
        assert isinstance(body, dict)
        assert body["exit_code"] == 0
        assert body["digest"]["schema"] == "ckdn.digest/2"
        assert body["digest"]["status"] == "pass"
        assert body["digest"]["rc"] == 0


@posix_run_only
@pytest.mark.asyncio
async def test_mcp_run_check_fail_is_not_tool_error(tmp_path: Path) -> None:
    cfg_path = _write_cfg(
        tmp_path,
        '[check.bad]\ncommand = "false"\nparser = "generic"\n',
    )
    mcp = create_server(config=cfg_path, cwd=tmp_path)
    async with Client(mcp) as client:
        failed = await client.call_tool(
            "run_check",
            {"check": "bad", "config": str(cfg_path)},
        )
        assert getattr(failed, "is_error", False) is False
        body = _data(failed)
        assert isinstance(body, dict)
        assert body["digest"]["status"] == "fail"
        assert body["exit_code"] != 0


@pytest.mark.asyncio
async def test_mcp_unknown_check_is_error(tmp_path: Path) -> None:
    cfg_path = _write_cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n',
    )
    mcp = create_server(config=cfg_path, cwd=tmp_path)
    async with Client(mcp) as client:
        with pytest.raises(Exception):  # noqa: B017 — tool error surface varies
            await client.call_tool(
                "run_check",
                {"check": "missing", "config": str(cfg_path)},
            )


@posix_run_only
@pytest.mark.asyncio
async def test_mcp_run_group_and_evidence(tmp_path: Path) -> None:
    cfg_path = _write_cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n'
        '[check.g]\nmembers = ["ok"]\n',
    )
    mcp = create_server(config=cfg_path, cwd=tmp_path)
    async with Client(mcp) as client:
        group = await client.call_tool(
            "run_group",
            {"alias": "g", "config": str(cfg_path)},
        )
        assert getattr(group, "is_error", False) is False
        gbody = _data(group)
        assert isinstance(gbody, dict)
        assert gbody["aggregate"]["alias"] == "g"
        assert gbody["exit_code"] == 0

        digest = await client.call_tool(
            "get_digest",
            {"config": str(cfg_path)},
        )
        dbody = _data(digest)
        assert isinstance(dbody, dict)
        assert dbody["schema"] == "ckdn.digest/2"

        runs = await client.call_tool(
            "list_runs",
            {"limit": 5, "config": str(cfg_path)},
        )
        rbody = _data(runs)
        assert isinstance(rbody, dict)
        assert rbody["runs"]

        evidence = await client.call_tool(
            "get_evidence",
            {"config": str(cfg_path), "artifact": "full.log", "limit": 5},
        )
        ebody = _data(evidence)
        assert isinstance(ebody, dict)
        assert ebody["artifact"]["name"] == "full.log"
        assert len(ebody["artifact"]["lines"]) <= 5


def test_mcp_lazy_exports() -> None:
    import ckdn.mcp as mcp_mod

    assert callable(mcp_mod.create_server)
    assert callable(mcp_mod.main)
    missing = "nope"
    with pytest.raises(AttributeError):
        getattr(mcp_mod, missing)


def test_server_context_env_and_load_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ckdn.app.errors import ConfigLoadError
    from ckdn.mcp.context import ServerContext

    cfg_path = _write_cfg(
        tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n'
    )
    ctx = ServerContext(default_config=cfg_path)
    assert ctx.resolve_config_path() == cfg_path
    assert ctx.load().checks["ok"].name == "ok"

    monkeypatch.delenv("CKDN_CONFIG", raising=False)
    bare = ServerContext()
    assert bare.resolve_config_path() is None
    monkeypatch.setenv("CKDN_CONFIG", str(cfg_path))
    assert bare.resolve_config_path() == Path(cfg_path)

    with pytest.raises(ConfigLoadError):
        ServerContext().load(str(tmp_path / "missing.toml"))


def test_mcp_main_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from ckdn.mcp import server as server_mod

    calls: list[dict[str, object]] = []

    class _Fake:
        def run(self, **kwargs: object) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(
        server_mod, "create_server", lambda config=None, cwd=None: _Fake()
    )
    server_mod.main(["--config", str(tmp_path / "ckdn.toml")])
    assert len(calls) == 1


def test_mcp_main_pins_stdio_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``ckdn-mcp`` must always run stdio, ignoring FASTMCP_TRANSPORT."""
    from ckdn.mcp import server as server_mod

    seen: list[object] = []

    class _Fake:
        def run(self, *, transport: object = None, **kwargs: object) -> None:
            seen.append(transport)

    monkeypatch.setenv("FASTMCP_TRANSPORT", "http")
    monkeypatch.setattr(
        server_mod, "create_server", lambda config=None, cwd=None: _Fake()
    )
    server_mod.main([])
    assert seen == ["stdio"]


@pytest.mark.asyncio
async def test_mcp_run_check_rejects_alias(tmp_path: Path) -> None:
    cfg_path = _write_cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n'
        '[check.g]\nmembers = ["ok"]\n',
    )
    mcp = create_server(config=cfg_path, cwd=tmp_path)
    async with Client(mcp) as client:
        with pytest.raises(Exception):  # noqa: B017
            await client.call_tool(
                "run_check",
                {"check": "g", "config": str(cfg_path)},
            )
        with pytest.raises(Exception):  # noqa: B017
            await client.call_tool(
                "run_group",
                {"alias": "ok", "config": str(cfg_path)},
            )
        with pytest.raises(Exception):  # noqa: B017
            await client.call_tool(
                "run_group",
                {"alias": "missing", "config": str(cfg_path)},
            )
