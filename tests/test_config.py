# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Config loading: atomic checks vs aliases."""

from __future__ import annotations

from pathlib import Path

import pytest

from ckdn.config import ConfigError, load_config


def _write(tmp: Path, body: str) -> Path:
    path = tmp / "ckdn.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_atomic_check_loads(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            '[check.ok]\ncommand = "true"\nparser = "generic"\n',
        )
    )
    check = cfg.checks["ok"]
    assert not check.is_alias
    assert check.command == "true"
    assert check.parser == "generic"


def test_alias_loads_members(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            "[check.a]\n"
            'command = "true"\n'
            'parser = "generic"\n'
            "[check.b]\n"
            'command = "false"\n'
            'parser = "generic"\n'
            "[check.group]\n"
            'members = ["a", "b"]\n'
            "fail_fast = false\n",
        )
    )
    group = cfg.checks["group"]
    assert group.is_alias
    assert group.members == ("a", "b")
    assert group.fail_fast is False
    assert group.command is None
    assert group.parser is None


def test_alias_defaults_fail_fast(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            "[check.a]\n"
            'command = "true"\n'
            'parser = "generic"\n'
            "[check.group]\n"
            'members = ["a"]\n',
        )
    )
    assert cfg.checks["group"].fail_fast is True


def test_ambiguous_command_and_members(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="ambiguous"):
        load_config(
            _write(
                tmp_path,
                "[check.bad]\n"
                'command = "true"\n'
                'parser = "generic"\n'
                'members = ["x"]\n',
            )
        )


def test_empty_members(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="non-empty"):
        load_config(_write(tmp_path, "[check.bad]\nmembers = []\n"))


def test_unknown_member(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown check"):
        load_config(
            _write(
                tmp_path,
                "[check.a]\n"
                'command = "true"\n'
                'parser = "generic"\n'
                "[check.group]\n"
                'members = ["missing"]\n',
            )
        )


def test_nested_alias_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="atomic"):
        load_config(
            _write(
                tmp_path,
                "[check.a]\n"
                'command = "true"\n'
                'parser = "generic"\n'
                "[check.inner]\n"
                'members = ["a"]\n'
                "[check.outer]\n"
                'members = ["inner"]\n',
            )
        )


def test_self_member_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="itself"):
        load_config(
            _write(
                tmp_path,
                "[check.a]\n"
                'command = "true"\n'
                'parser = "generic"\n'
                "[check.loop]\n"
                'members = ["a", "loop"]\n',
            )
        )


def test_duplicate_member_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="more than once"):
        load_config(
            _write(
                tmp_path,
                "[check.a]\n"
                'command = "true"\n'
                'parser = "generic"\n'
                "[check.group]\n"
                'members = ["a", "a"]\n',
            )
        )


def test_fail_fast_on_atomic_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="fail_fast"):
        load_config(
            _write(
                tmp_path,
                "[check.a]\n"
                'command = "true"\n'
                'parser = "generic"\n'
                "fail_fast = true\n",
            )
        )


def test_starter_config_loads(tmp_path: Path) -> None:
    from ckdn.config import STARTER_CONFIG

    path = _write(tmp_path, STARTER_CONFIG)
    cfg = load_config(path)
    assert cfg.checks["lint"].is_alias
    assert cfg.checks["lint"].members == ("ruff",)
    assert cfg.checks["ruff"].parser == "ruff"
    assert cfg.checks["types"].is_alias
    assert cfg.checks["types"].members == ("ty", "mypy")
    assert cfg.checks["mypy"].parser == "mypy"
    assert cfg.checks["ty"].parser == "ty"


def test_empty_check_table(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="requires command and parser"):
        load_config(_write(tmp_path, "[check.bad]\n"))


def test_alias_timeout_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="must not set"):
        load_config(
            _write(
                tmp_path,
                "[check.a]\ncommand = \"true\"\nparser = \"generic\"\n"
                "[check.g]\nmembers = [\"a\"]\ntimeout = 1\n",
            )
        )


def test_alias_non_string_members(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="non-empty strings"):
        load_config(_write(tmp_path, "[check.bad]\nmembers = [1, 2]\n"))


def test_alias_unexpected_option(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unexpected keys"):
        load_config(
            _write(
                tmp_path,
                "[check.a]\ncommand = \"true\"\nparser = \"generic\"\n"
                "[check.g]\nmembers = [\"a\"]\ntop = 5\n",
            )
        )


def test_atomic_missing_parser(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="requires command and parser"):
        load_config(_write(tmp_path, '[check.a]\ncommand = "true"\n'))


def test_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "ckdn.toml"
    path.write_text("[run\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(path)


def test_run_not_table(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="\\[run\\] must be a table"):
        load_config(_write(tmp_path, 'run = "x"\n[check.a]\ncommand="true"\nparser="generic"\n'))


def test_no_checks(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no \\[check"):
        load_config(_write(tmp_path, "[run]\nkeep = 1\n"))


def test_check_not_table(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="must be a table"):
        load_config(_write(tmp_path, "[run]\nkeep = 1\n[check]\na = \"hi\"\n"))


def test_timeout_parsed(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            '[check.a]\ncommand = "true"\nparser = "generic"\ntimeout = 12.5\n',
        )
    )
    assert cfg.checks["a"].timeout == 12.5
