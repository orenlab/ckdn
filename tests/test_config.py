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


def test_check_env_parsed(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            '[check.a]\ncommand = "true"\nparser = "generic"\n'
            'env = { FOO = "bar", OUT = "{run_dir}/x" }\n',
        ),
        cwd=tmp_path,
    )
    assert cfg.checks["a"].env == {"FOO": "bar", "OUT": "{run_dir}/x"}
    # env is not leaked into the parser options bag
    assert "env" not in cfg.checks["a"].options


def test_check_env_must_be_string_values(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="env"):
        load_config(
            _write(
                tmp_path,
                '[check.a]\ncommand = "true"\nparser = "generic"\nenv = { N = 1 }\n',
            ),
            cwd=tmp_path,
        )


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
                '[check.bad]\ncommand = "true"\nparser = "generic"\nmembers = ["x"]\n',
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
                '[check.a]\ncommand = "true"\nparser = "generic"\nfail_fast = true\n',
            )
        )


def test_starter_config_loads(tmp_path: Path) -> None:
    from ckdn.config import STARTER_CONFIG

    path = _write(tmp_path, STARTER_CONFIG)
    cfg = load_config(path)
    assert cfg.checks["lint"].is_alias
    assert cfg.checks["lint"].members == ("ruff",)
    assert cfg.checks["ruff"].parser == "ruff"
    assert cfg.checks["format"].parser == "reformat"
    assert cfg.checks["pre_commit"].parser == "pre_commit"
    assert cfg.checks["lock"].parser == "generic"
    assert cfg.checks["style"].members == ("format", "ruff")
    assert cfg.checks["hooks"].members == ("pre_commit",)
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
                '[check.a]\ncommand = "true"\nparser = "generic"\n'
                '[check.g]\nmembers = ["a"]\ntimeout = 1\n',
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
                '[check.a]\ncommand = "true"\nparser = "generic"\n'
                '[check.g]\nmembers = ["a"]\ntop = 5\n',
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
        load_config(
            _write(tmp_path, 'run = "x"\n[check.a]\ncommand="true"\nparser="generic"\n')
        )


def test_no_checks(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no \\[check"):
        load_config(_write(tmp_path, "[run]\nkeep = 1\n"))


def test_check_not_table(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="must be a table"):
        load_config(_write(tmp_path, '[run]\nkeep = 1\n[check]\na = "hi"\n'))


def test_timeout_parsed(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            '[check.a]\ncommand = "true"\nparser = "generic"\ntimeout = 12.5\n',
        )
    )
    assert cfg.checks["a"].timeout == 12.5


def test_cwd_separate_from_config_path(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    worktree = tmp_path / "wt"
    config_dir.mkdir()
    worktree.mkdir()
    path = config_dir / "ckdn.toml"
    path.write_text('[check.ok]\ncommand = "true"\nparser = "generic"\n')
    cfg = load_config(path, cwd=worktree)
    assert cfg.config_path == path.resolve()
    assert cfg.cwd == worktree.resolve()
    assert cfg.runs_dir == worktree / ".agent-runs"


def test_root_is_an_alias_for_cwd(tmp_path: Path) -> None:
    cfg = load_config(
        _write(tmp_path, '[check.a]\ncommand = "true"\nparser = "generic"\n'),
        cwd=tmp_path,
    )
    assert cfg.root == cfg.cwd == tmp_path.resolve()


def test_command_allowlist_prefixes_parsed(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            '[run]\ncommand_policy = "allowlist"\n'
            '[run.command_allowlist]\nprefixes = ["make ", "./scripts/"]\n\n'
            '[check.a]\ncommand = "make test"\nparser = "generic"\n',
        ),
        cwd=tmp_path,
    )
    assert cfg.run.command_allowlist == ("make ", "./scripts/")


def test_command_allowlist_without_prefixes_falls_back_to_defaults(
    tmp_path: Path,
) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            "[run.command_allowlist]\nother = 1\n\n"
            '[check.a]\ncommand = "true"\nparser = "generic"\n',
        ),
        cwd=tmp_path,
    )
    assert cfg.run.command_allowlist is None


@pytest.mark.parametrize(
    "table",
    [
        'command_allowlist = "nope"',
        "[run.command_allowlist]\nprefixes = []",
        '[run.command_allowlist]\nprefixes = "uv run "',
        "[run.command_allowlist]\nprefixes = [1, 2]",
        '[run.command_allowlist]\nprefixes = ["", "uv run "]',
    ],
)
def test_command_allowlist_rejects_bad_shapes(tmp_path: Path, table: str) -> None:
    body = (
        f"[run]\n{table}\n\n"
        if table.startswith("command_allowlist")
        else f"[run]\n\n{table}\n\n"
    )
    with pytest.raises(ConfigError, match="command_allowlist"):
        load_config(
            _write(
                tmp_path,
                body + '[check.a]\ncommand = "true"\nparser = "generic"\n',
            ),
            cwd=tmp_path,
        )


def test_members_is_rejected_on_an_atomic_check(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="ambiguous"):
        load_config(
            _write(
                tmp_path,
                '[check.a]\ncommand = "true"\nparser = "generic"\nmembers = ["b"]\n',
            ),
            cwd=tmp_path,
        )


def test_check_env_must_be_a_table(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="env must be a table"):
        load_config(
            _write(
                tmp_path,
                '[check.a]\ncommand = "true"\nparser = "generic"\nenv = "FOO=bar"\n',
            ),
            cwd=tmp_path,
        )


_ATOMIC = '[check.a]\ncommand = "true"\nparser = "generic"\n'

_RUN_INT_DEFAULTS = {
    "keep": 20,
    "top": 20,
    "max_snippet_lines": 12,
    "log_tail_lines": 40,
}


@pytest.mark.parametrize(
    "literal",
    ['"60s"', '"12.5"', "true", "false", "[]", "{ s = 1 }"],
)
def test_timeout_rejects_non_numbers(tmp_path: Path, literal: str) -> None:
    """A mistyped timeout is a ConfigError, not a raw ValueError traceback."""
    with pytest.raises(ConfigError) as exc:
        load_config(
            _write(tmp_path, _ATOMIC + f"timeout = {literal}\n"),
            cwd=tmp_path,
        )
    assert str(exc.value) == "[check.a] timeout must be a number"


@pytest.mark.parametrize(("literal", "expected"), [("12.5", 12.5), ("60", 60.0)])
def test_timeout_accepts_int_and_float(
    tmp_path: Path, literal: str, expected: float
) -> None:
    cfg = load_config(
        _write(tmp_path, _ATOMIC + f"timeout = {literal}\n"), cwd=tmp_path
    )
    timeout = cfg.checks["a"].timeout
    assert timeout == expected
    assert isinstance(timeout, float)


def test_timeout_absent_stays_none(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, _ATOMIC), cwd=tmp_path)
    assert cfg.checks["a"].timeout is None


@pytest.mark.parametrize("key", sorted(_RUN_INT_DEFAULTS))
@pytest.mark.parametrize("literal", ['"twenty"', '"20"', "true", "false", "1.5", "[]"])
def test_run_int_settings_reject_non_integers(
    tmp_path: Path, key: str, literal: str
) -> None:
    """A mistyped [run] count is a ConfigError, not a raw ValueError."""
    with pytest.raises(ConfigError) as exc:
        load_config(
            _write(tmp_path, f"[run]\n{key} = {literal}\n\n" + _ATOMIC),
            cwd=tmp_path,
        )
    assert str(exc.value) == f"[run].{key} must be an integer"


@pytest.mark.parametrize(("key", "default"), sorted(_RUN_INT_DEFAULTS.items()))
def test_run_int_settings_defaults(tmp_path: Path, key: str, default: int) -> None:
    cfg = load_config(_write(tmp_path, _ATOMIC), cwd=tmp_path)
    assert getattr(cfg.run, key) == default


@pytest.mark.parametrize("key", sorted(_RUN_INT_DEFAULTS))
def test_run_int_settings_read_the_configured_key(tmp_path: Path, key: str) -> None:
    # 7 differs from every default, so reading the wrong key is visible.
    cfg = load_config(_write(tmp_path, f"[run]\n{key} = 7\n\n" + _ATOMIC), cwd=tmp_path)
    assert getattr(cfg.run, key) == 7


@pytest.mark.parametrize("literal", ['"false"', '"true"', "0", "1", "[]"])
def test_alias_fail_fast_must_be_a_boolean(tmp_path: Path, literal: str) -> None:
    """`fail_fast = "false"` used to coerce to True and silence a member."""
    with pytest.raises(ConfigError) as exc:
        load_config(
            _write(
                tmp_path,
                _ATOMIC + f'[check.g]\nmembers = ["a"]\nfail_fast = {literal}\n',
            ),
            cwd=tmp_path,
        )
    assert str(exc.value) == "[check.g] fail_fast must be a boolean"


def test_ckdn_cwd_env_var_selects_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = _write(tmp_path, '[check.a]\ncommand = "true"\nparser = "generic"\n')
    monkeypatch.setenv("CKDN_CWD", str(project))
    cfg = load_config(path)
    assert cfg.cwd == project.resolve()
    # An explicit cwd still wins over the environment.
    assert load_config(path, cwd=tmp_path).cwd == tmp_path.resolve()
