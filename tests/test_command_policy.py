# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Command policy, lock file, and verify-config coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
from worktree_fixtures import make_worktree_slice

from ckdn.command_policy import (
    CommandPolicyError,
    _is_sensitive_path,
    _looks_like_path,
    command_matches_allowlist,
    validate_command,
    validate_command_tokens,
)
from ckdn.config import Config, ConfigError, load_config
from ckdn.config_lock import (
    LOCK_NAME,
    command_digest,
    verify_config,
    verify_config_lock,
    write_config_lock,
)
from ckdn.parsers.base import ParseContext, artifact_path
from ckdn.parsers.pytest_junit import PytestJUnitParser
from ckdn.runner import RC_POLICY, build_tokens


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
        cwd=cwd,
        policy="workspace",
        tokens=tokens,
    )


def test_workspace_blocks_absolute_outside_cwd(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    with pytest.raises(CommandPolicyError, match="escapes workspace"):
        validate_command(
            "cat /etc/passwd",
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
            cwd=cwd,
            policy="workspace",
            tokens=build_tokens("head ../../.ssh/id_rsa", cwd / "run", []),
        )


def test_workspace_blocks_extra_args_escape(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    with pytest.raises(CommandPolicyError, match="escapes workspace"):
        validate_command(
            "uv run pytest",
            cwd=cwd,
            policy="workspace",
            # The escape rides in on the extra args, which reach the policy
            # inside the built argv -- the same way `ckdn run … -- -x` and MCP
            # `extra_args` do.
            tokens=build_tokens("uv run pytest", cwd / "run", ["/etc/passwd"]),
        )


def test_policy_off_allows_escape(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    validate_command(
        "cat /etc/passwd",
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
            cwd=cwd,
            policy="allowlist",
            tokens=build_tokens("cat /etc/passwd", cwd / "run", []),
        )


def test_allowlist_allows_uv_run(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    validate_command(
        "uv run ruff check .",
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


# --- allowlist matching ------------------------------------------------------


def test_allowlist_prefix_forms() -> None:
    prefixes = ("uv run ", "make")
    # A prefix ending in a space matches only as a prefix.
    assert command_matches_allowlist("uv run pytest", prefixes) is True
    assert command_matches_allowlist("uv runner", prefixes) is False
    # One without a trailing space matches the bare word or word + argument,
    # never a longer word that merely starts the same way.
    assert command_matches_allowlist("make", prefixes) is True
    assert command_matches_allowlist("make test", prefixes) is True
    assert command_matches_allowlist("makefile-thing", prefixes) is False
    assert command_matches_allowlist("   ", prefixes) is False


def test_allowlist_rejects_command_outside_the_prefixes(tmp_path: Path) -> None:
    with pytest.raises(CommandPolicyError, match="allowlist prefix"):
        validate_command(
            "curl http://example.test",
            cwd=tmp_path,
            policy="allowlist",
            allowlist_prefixes=("uv run ",),
            tokens=["curl", "http://example.test"],
        )


def test_allowlist_still_confines_path_arguments(tmp_path: Path) -> None:
    # The executable is allowed; the path argument is not.
    with pytest.raises(CommandPolicyError, match="escapes workspace"):
        validate_command(
            "uv run cat /etc/passwd",
            cwd=tmp_path,
            policy="allowlist",
            allowlist_prefixes=("uv run ",),
            tokens=["uv", "run", "cat", "/etc/passwd"],
        )


def test_policy_off_accepts_anything(tmp_path: Path) -> None:
    tokens = ["cat", "/etc/passwd"]
    assert (
        validate_command("cat /etc/passwd", cwd=tmp_path, policy="off", tokens=tokens)
        == tokens
    )


# --- what counts as a path in argv -------------------------------------------


def test_flag_values_are_unwrapped_and_bare_flags_ignored(tmp_path: Path) -> None:
    # `--flag=<path>` is validated on its value; a bare flag is not a path.
    with pytest.raises(CommandPolicyError, match="escapes workspace"):
        validate_command_tokens(
            ["--config=/etc/shadow"], cwd=tmp_path, policy="workspace"
        )
    validate_command_tokens(["--verbose", "-x", ""], cwd=tmp_path, policy="workspace")


def test_scheme_prefixed_values_are_unwrapped(tmp_path: Path) -> None:
    # pylint's `json2:<path>` output spec, and friends.
    with pytest.raises(CommandPolicyError, match="escapes workspace"):
        validate_command_tokens(
            ["--output-format=json2:/etc/passwd"], cwd=tmp_path, policy="workspace"
        )
    # A colon that is not a path spec stays one opaque token.
    validate_command_tokens(["key:value"], cwd=tmp_path, policy="workspace")


def test_non_path_tokens_are_left_alone(tmp_path: Path) -> None:
    validate_command_tokens(["pytest", "-q", "tests"], cwd=tmp_path, policy="workspace")


def test_only_the_workspace_policy_inspects_paths(tmp_path: Path) -> None:
    # `validate_command_tokens` is a no-op under any other policy.
    validate_command_tokens(["/etc/passwd"], cwd=tmp_path, policy="off")


def test_home_secret_directories_are_refused_even_inside_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A workspace that happens to be $HOME: `.ssh` is inside it and contained,
    # so only the sensitive-location rule can stop it.
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    with pytest.raises(CommandPolicyError, match="sensitive location"):
        validate_command_tokens(["./.ssh/id_ed25519"], cwd=home, policy="workspace")
    validate_command_tokens(["./src/main.py"], cwd=home, policy="workspace")


def test_a_missing_home_does_not_break_path_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_home(cls: type[Path]) -> Path:
        raise RuntimeError("home directory cannot be determined")

    monkeypatch.setattr(Path, "home", classmethod(_no_home))
    validate_command_tokens(["./src/main.py"], cwd=tmp_path, policy="workspace")


# --- lock file ---------------------------------------------------------------


def _load(tmp_path: Path, body: str, *, policy: str = "workspace") -> Config:
    return load_config(_cfg(tmp_path, body, policy=policy), cwd=tmp_path)


def test_lock_file_skips_aliases(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        '[check.a]\ncommand = "true"\nparser = "generic"\n\n'
        '[check.group]\nmembers = ["a"]\n',
    )
    lock = write_config_lock(cfg)
    text = lock.read_text(encoding="utf-8")
    assert "[check.a]" in text
    assert "group" not in text
    assert verify_config_lock(cfg) == []


def test_verify_reports_a_missing_lock_file(tmp_path: Path) -> None:
    cfg = _load(tmp_path, '[check.a]\ncommand = "true"\nparser = "generic"\n')
    errors = verify_config_lock(cfg)
    assert len(errors) == 1
    assert "lock file not found" in errors[0]


def test_verify_reports_an_unreadable_lock_file(tmp_path: Path) -> None:
    cfg = _load(tmp_path, '[check.a]\ncommand = "true"\nparser = "generic"\n')
    (tmp_path / LOCK_NAME).write_text("not = = toml", encoding="utf-8")
    errors = verify_config_lock(cfg)
    assert len(errors) == 1
    assert "invalid TOML" in errors[0]


def test_verify_reports_a_lock_file_without_check_tables(tmp_path: Path) -> None:
    cfg = _load(tmp_path, '[check.a]\ncommand = "true"\nparser = "generic"\n')
    (tmp_path / LOCK_NAME).write_text('check = "not a table"\n', encoding="utf-8")
    assert verify_config_lock(cfg) == [
        f"{tmp_path / LOCK_NAME}: missing [check.<name>] tables"
    ]


def test_verify_reports_missing_entries_and_digests(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        '[check.a]\ncommand = "true"\nparser = "generic"\n\n'
        '[check.b]\ncommand = "false"\nparser = "generic"\n',
    )
    (tmp_path / LOCK_NAME).write_text(
        '[check.b]\ncommand_sha256 = ""\n[check.gone]\ncommand_sha256 = "x"\n',
        encoding="utf-8",
    )
    errors = verify_config_lock(cfg)
    assert any("[check.a] missing from" in e for e in errors)
    assert any("[check.b] missing command_sha256" in e for e in errors)
    assert any(
        "[check.gone] present in lock file but not in config" in e for e in errors
    )


def test_verify_reports_a_changed_command(tmp_path: Path) -> None:
    cfg = _load(tmp_path, '[check.a]\ncommand = "true"\nparser = "generic"\n')
    (tmp_path / LOCK_NAME).write_text(
        f'[check.a]\ncommand_sha256 = "{command_digest("something else")}"\n',
        encoding="utf-8",
    )
    errors = verify_config_lock(cfg)
    assert len(errors) == 1
    assert "command changed since lock" in errors[0]


def test_verify_config_reports_policy_violations_per_check(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        '[check.bad]\ncommand = "cat /etc/passwd"\nparser = "generic"\n\n'
        '[check.group]\nmembers = ["bad"]\n',
    )
    errors = verify_config(cfg)
    # Reported once, against the atomic check -- not again via the alias.
    assert len(errors) == 1
    assert errors[0].startswith("[check.bad]")


def test_verify_config_combines_policy_and_lock_errors(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        '[check.bad]\ncommand = "cat /etc/passwd"\nparser = "generic"\n',
    )
    errors = verify_config(cfg, locked=True)
    assert len(errors) == 2
    assert any("escapes workspace" in e for e in errors)
    assert any("lock file not found" in e for e in errors)


def test_sensitive_roots_and_path_shapes_directly() -> None:
    # Reached through validate only when the workspace itself sits under one of
    # these roots, which no test can arrange; the rule is the unit here.
    assert _is_sensitive_path(Path("/etc/passwd")) is True
    assert _is_sensitive_path(Path("/proc")) is True
    assert _is_sensitive_path(Path("/srv/app/main.py")) is False
    assert _looks_like_path("") is False
    assert _looks_like_path("..") is True
    assert _looks_like_path("src/main.py") is True
    assert _looks_like_path("pytest") is False
