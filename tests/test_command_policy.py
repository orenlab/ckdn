# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Command policy, lock file, and verify-config coverage."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from worktree_fixtures import make_worktree_slice

from ckdn.command_policy import (
    CommandPolicyError,
    command_matches_allowlist,
    validate_command,
)
from ckdn.config import ConfigError, load_config
from ckdn.config_lock import command_digest, verify_config, write_config_lock
from ckdn.parsers.base import ParseContext, artifact_path
from ckdn.parsers.pytest_junit import PytestJUnitParser
from ckdn.runner import RC_POLICY, build_tokens

# These assert POSIX absolute/sensitive-path denial (/etc/passwd); on Windows
# such paths are not absolute, so containment behaves differently.
posix_paths_only = pytest.mark.skipif(
    os.name == "nt", reason="POSIX absolute/sensitive path semantics"
)


def _cfg(tmp_path: Path, body: str, *, policy: str = "workspace") -> Path:
    path = tmp_path / "ckdn.toml"
    path.write_text(
        f'[run]\ncommand_policy = "{policy}"\n\n{body}',
        encoding="utf-8",
    )
    return path


def test_workspace_allows_starter_style_commands(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    run_dir = cwd / ".agent-runs" / "run"
    tokens = build_tokens(
        "uv run pytest -q --junitxml {run_dir}/junit.xml",
        run_dir,
        [],
    )
    validate_command(
        "uv run pytest -q --junitxml {run_dir}/junit.xml",
        [],
        cwd=cwd,
        policy="workspace",
        tokens=tokens,
    )


@posix_paths_only
def test_workspace_blocks_absolute_outside_cwd(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    with pytest.raises(CommandPolicyError, match="escapes workspace"):
        validate_command(
            "cat /etc/passwd",
            [],
            cwd=cwd,
            policy="workspace",
            tokens=build_tokens("cat /etc/passwd", cwd / "run", []),
        )


def test_workspace_blocks_parent_traversal(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    with pytest.raises(CommandPolicyError, match="escapes workspace"):
        validate_command(
            "head ../../.ssh/id_rsa",
            [],
            cwd=cwd,
            policy="workspace",
            tokens=build_tokens("head ../../.ssh/id_rsa", cwd / "run", []),
        )


@posix_paths_only
def test_workspace_blocks_extra_args_escape(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    with pytest.raises(CommandPolicyError, match="escapes workspace"):
        validate_command(
            "uv run pytest",
            ["/etc/passwd"],
            cwd=cwd,
            policy="workspace",
            tokens=build_tokens("uv run pytest", cwd / "run", ["/etc/passwd"]),
        )


def test_policy_off_allows_escape(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    validate_command(
        "cat /etc/passwd",
        [],
        cwd=cwd,
        policy="off",
        tokens=build_tokens("cat /etc/passwd", cwd / "run", []),
    )


def test_allowlist_blocks_unknown_executable(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    with pytest.raises(CommandPolicyError, match="allowlist"):
        validate_command(
            "cat /etc/passwd",
            [],
            cwd=cwd,
            policy="allowlist",
            tokens=build_tokens("cat /etc/passwd", cwd / "run", []),
        )


def test_allowlist_allows_uv_run(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    validate_command(
        "uv run ruff check .",
        [],
        cwd=cwd,
        policy="allowlist",
        tokens=build_tokens("uv run ruff check .", cwd / "run", []),
    )


def test_allowlist_custom_prefix(tmp_path: Path) -> None:
    assert command_matches_allowlist("make test", ("make ",))
    assert not command_matches_allowlist("cmake test", ("make ",))


def test_config_parses_command_policy_and_allowlist(tmp_path: Path) -> None:
    path = _cfg(
        tmp_path,
        '[run.command_allowlist]\nprefixes = ["make "]\n'
        '[check.ok]\ncommand = "make test"\nparser = "generic"\n',
        policy="allowlist",
    )
    cfg = load_config(path, cwd=tmp_path)
    assert cfg.run.command_policy == "allowlist"
    assert cfg.run.command_allowlist == ("make ",)


def test_invalid_command_policy_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ckdn.toml"
    path.write_text(
        '[run]\ncommand_policy = "paranoid"\n[check.a]\n'
        'command = "true"\nparser = "generic"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="command_policy"):
        load_config(path, cwd=tmp_path)


def test_lock_and_verify_config(tmp_path: Path) -> None:
    path = _cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n',
    )
    cfg = load_config(path, cwd=tmp_path)
    lock_path = write_config_lock(cfg)
    assert verify_config(cfg, locked=True, lock_path=lock_path) == []
    assert verify_config(cfg) == []

    tampered = tmp_path / "ckdn.toml"
    tampered.write_text(
        '[run]\n[check.ok]\ncommand = "false"\nparser = "generic"\n',
        encoding="utf-8",
    )
    cfg2 = load_config(tampered, cwd=tmp_path)
    errors = verify_config(cfg2, locked=True, lock_path=lock_path)
    assert any("command changed" in err for err in errors)


def test_command_digest_stable() -> None:
    assert command_digest("uv run pytest") == command_digest("uv run pytest")
    assert command_digest("uv run pytest") != command_digest("uv run ruff check .")


def test_worktree_slice_command_and_artifact_paths(tmp_path: Path) -> None:
    """Config in /tmp-style dir, cwd = worktree: policy + artifact paths stay valid."""
    slice_ = make_worktree_slice(
        tmp_path,
        body=(
            '[run]\nruns_dir = ".agent-runs"\n\n'
            "[check.pt]\n"
            'command = "uv run pytest -q --junitxml {run_dir}/junit.xml"\n'
            'parser = "pytest"\n'
        ),
    )
    cfg = load_config(slice_.cfg_path, cwd=slice_.worktree)
    run_dir = cfg.runs_dir / "20260713T000000Z-pt"
    run_dir.mkdir(parents=True)
    check = cfg.checks["pt"]
    assert check.command is not None
    tokens = build_tokens(check.command, run_dir, [])
    validate_command(
        check.command,
        [],
        cwd=cfg.cwd,
        policy=cfg.run.command_policy,
        tokens=tokens,
    )
    junit = artifact_path(run_dir, "{run_dir}/junit.xml")
    assert junit.is_relative_to(run_dir.resolve())
    (junit.parent).mkdir(parents=True, exist_ok=True)
    (junit).write_text(
        '<?xml version="1.0"?><testsuites><testsuite tests="0"/></testsuites>',
        encoding="utf-8",
    )
    result = PytestJUnitParser().parse(
        ParseContext(
            run_dir=run_dir,
            log_text="",
            rc=0,
            options={},
            top=20,
            max_snippet_lines=12,
        )
    )
    assert result.parser_ok
    assert cfg.runs_dir == slice_.worktree / ".agent-runs"


@posix_paths_only
def test_run_one_policy_violation_no_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ckdn.app import run_one
    from ckdn.config import load_config

    path = _cfg(
        tmp_path,
        '[check.bad]\ncommand = "cat /etc/passwd"\nparser = "generic"\n',
    )
    cfg = load_config(path, cwd=tmp_path)

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr("ckdn.app.run.execute", _boom)
    result = run_one(cfg, cfg.checks["bad"], extra=[])
    assert result.rc == RC_POLICY
    assert result.status == "error"
    assert any("escapes workspace" in note for note in result.digest.get("notes", []))
