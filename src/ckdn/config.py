# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Configuration loading for ckdn (``ckdn.toml``)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_NAME = "ckdn.toml"

# Emitted verbatim by `ckdn init`. Built with implicit string concatenation so
# each source line stays within the line-length limit; the generated TOML keeps
# every command on a single line. Keys that are not part of the default starter
# remain present as comments so the file is a complete option catalogue.
STARTER_CONFIG = (
    '# ckdn configuration. Docs: see README.md ("Configuration").\n'
    "#\n"
    "# Atomic check: command + parser (+ optional timeout / parser options).\n"
    "# Alias: members = [\"atomic\", ...] (+ optional fail_fast; default true).\n"
    "# `{run_dir}` is substituted in commands and artifact path options.\n"
    "# Per-check `top` overrides [run].top for that digest only.\n"
    "\n"
    "[run]\n"
    'runs_dir = ".agent-runs"   # where run artifacts live (add to .gitignore)\n'
    "keep = 20                  # prune old run directories beyond this count\n"
    "top = 20                   # max findings / top entries in a digest\n"
    "max_snippet_lines = 12     # max detail lines per finding\n"
    "log_tail_lines = 40        # log tail size on error / parse_mismatch\n"
    "\n"
    "# --- enabled -------------------------------------------------------\n"
    "\n"
    "[check.pytest]\n"
    'command = "uv run pytest -q --junitxml {run_dir}/junit.xml"\n'
    'parser = "pytest"\n'
    "# timeout = 60\n"
    "# top = 20\n"
    '# junit = "junit.xml"      # artifact path (default); may use {run_dir}\n'
    "\n"
    "[check.coverage]\n"
    'command = "uv run pytest -q --junitxml {run_dir}/junit.xml '
    "--cov=src --cov-report=term-missing "
    '--cov-report=xml:{run_dir}/coverage.xml"\n'
    'parser = "coverage"\n'
    "fail_under = 96.0\n"
    "# missing_lines_preview = 40\n"
    "# timeout = 120\n"
    "# top = 20\n"
    '# coverage_xml = "coverage.xml"\n'
    '# junit = "junit.xml"\n'
    "\n"
    "[check.ty]\n"
    'command = "uvx ty check"\n'
    'parser = "ty"\n'
    "# timeout = 60\n"
    "# top = 20\n"
    "\n"
    "[check.mypy]\n"
    'command = "uv run mypy src --output json"\n'
    '# # command = "uv run mypy src"  # text mode (format = "text")\n'
    'parser = "mypy"\n'
    'format = "json"\n'
    "# timeout = 120\n"
    "# top = 20\n"
    "\n"
    "[check.types]\n"
    'members = ["ty", "mypy"]\n'
    "# fail_fast = false         # default true; false runs all members\n"
    '# # members = ["ty", "mypy", "pyright"]  # after enabling pyright\n'
    "\n"
    "[check.ruff]\n"
    'command = "uv run ruff check --output-format json '
    '--output-file {run_dir}/ruff.json ."\n'
    'parser = "ruff"\n'
    "# timeout = 60\n"
    "# top = 20\n"
    '# report = "ruff.json"\n'
    "\n"
    "[check.lint]\n"
    'members = ["ruff"]\n'
    "# fail_fast = false\n"
    '# # members = ["ruff", "pylint", "bandit"]\n'
    "\n"
    "# --- optional parsers (uncomment to enable) -----------------------\n"
    "\n"
    "# [check.pyright]\n"
    '# command = "uvx pyright --outputjson"\n'
    '# parser = "pyright"\n'
    "# timeout = 120\n"
    "# top = 20\n"
    "\n"
    "# [check.format]\n"
    '# command = "uv run ruff format --check ."\n'
    '# # command = "uv run black --check src tests"\n'
    '# parser = "reformat"\n'
    "# timeout = 60\n"
    "# top = 20\n"
    "\n"
    "# [check.pip_audit]\n"
    '# command = "uv run pip-audit --progress-spinner off '
    '-f json -o {run_dir}/pip-audit.json"\n'
    '# parser = "pip_audit"\n'
    "# timeout = 120\n"
    "# top = 20\n"
    '# report = "pip-audit.json"\n'
    "\n"
    "# [check.bandit]\n"
    '# command = "uv run bandit -r src -f json '
    '-o {run_dir}/bandit.json"\n'
    '# # Filter severity TOOL-SIDE, e.g. --severity-level medium\n'
    '# parser = "bandit"\n'
    "# timeout = 120\n"
    "# top = 20\n"
    '# report = "bandit.json"\n'
    "\n"
    "# [check.pylint]\n"
    '# command = "uv run pylint src '
    '--output-format=json2:{run_dir}/pylint.json"\n'
    '# # Also recommend --fail-under on the pylint CLI.\n'
    '# parser = "pylint"\n'
    "# score_fail_under = 8.0\n"
    "# timeout = 180\n"
    "# top = 20\n"
    '# report = "pylint.json"\n'
    "\n"
    "# [check.sarif]\n"
    '# command = "uvx semgrep scan --config auto '
    '--sarif-output {run_dir}/report.sarif ."\n'
    '# parser = "sarif"\n'
    '# fail_levels = ["error"]   # add "warning" if tool exits nonzero on warnings\n'
    "# timeout = 300\n"
    "# top = 20\n"
    '# report = "report.sarif"\n'
    "\n"
    "# --- recipes (generic parser; exit-code only) ---------------------\n"
    "\n"
    "# [check.docs]\n"
    '# command = "uv run sphinx-build -W --keep-going -b html '
    'docs {run_dir}/docs-build"\n'
    '# parser = "generic"\n'
    "# timeout = 300\n"
    "\n"
    "# [check.build]\n"
    '# command = "uv build"\n'
    '# parser = "generic"\n'
    "# timeout = 300\n"
    "\n"
    "# [check.twine]\n"
    '# command = "uvx twine check dist/*"\n'
    '# parser = "generic"\n'
    "# timeout = 60\n"
    "\n"
    "# [check.pip_check]\n"
    '# command = "uv run pip check"\n'
    '# parser = "generic"\n'
    "# timeout = 60\n"
)


class ConfigError(Exception):
    """Raised for a missing or invalid configuration file."""


@dataclass(frozen=True)
class RunSettings:
    runs_dir: Path = Path(".agent-runs")
    keep: int = 20
    top: int = 20
    max_snippet_lines: int = 12
    log_tail_lines: int = 40


@dataclass(frozen=True)
class CheckConfig:
    """One configured check: either atomic (command+parser) or an alias."""

    name: str
    command: str | None = None
    parser: str | None = None
    timeout: float | None = None
    options: dict[str, Any] = field(default_factory=dict)
    members: tuple[str, ...] | None = None
    fail_fast: bool = True

    @property
    def is_alias(self) -> bool:
        return self.members is not None


@dataclass(frozen=True)
class Config:
    root: Path
    run: RunSettings
    checks: dict[str, CheckConfig]

    @property
    def runs_dir(self) -> Path:
        d = self.run.runs_dir
        return d if d.is_absolute() else self.root / d


_ATOMIC_RESERVED = frozenset({"command", "parser", "timeout"})
_ALIAS_RESERVED = frozenset({"members", "fail_fast"})


def _parse_check(name: str, raw: dict[str, Any]) -> CheckConfig:
    has_command = "command" in raw
    has_parser = "parser" in raw
    has_members = "members" in raw
    is_alias = has_members
    is_atomic = has_command or has_parser

    if is_alias and is_atomic:
        raise ConfigError(
            f"[check.{name}] is ambiguous: use either command+parser "
            "(atomic) or members (alias), not both"
        )
    if not is_alias and not is_atomic:
        raise ConfigError(
            f"[check.{name}] requires command and parser, or members"
        )

    if is_alias:
        for key in ("command", "parser", "timeout"):
            if key in raw:
                raise ConfigError(
                    f"[check.{name}] alias must not set `{key}`"
                )
        members_raw = raw["members"]
        if not isinstance(members_raw, list) or not members_raw:
            raise ConfigError(
                f"[check.{name}] members must be a non-empty array of check names"
            )
        if not all(isinstance(m, str) and m for m in members_raw):
            raise ConfigError(
                f"[check.{name}] members must be non-empty strings"
            )
        fail_fast = bool(raw.get("fail_fast", True))
        options = {
            k: v for k, v in raw.items() if k not in _ALIAS_RESERVED
        }
        if options:
            raise ConfigError(
                f"[check.{name}] alias only allows members and fail_fast; "
                f"unexpected keys: {', '.join(sorted(options))}"
            )
        return CheckConfig(
            name=name,
            members=tuple(str(m) for m in members_raw),
            fail_fast=fail_fast,
        )

    command = raw.get("command")
    parser = raw.get("parser")
    if not command or not parser:
        raise ConfigError(f"[check.{name}] requires command and parser")
    if "fail_fast" in raw:
        raise ConfigError(
            f"[check.{name}] fail_fast is only valid on aliases"
        )
    if "members" in raw:
        raise ConfigError(
            f"[check.{name}] members is only valid on aliases"
        )
    timeout_raw = raw.get("timeout")
    timeout = float(timeout_raw) if timeout_raw is not None else None
    options = {k: v for k, v in raw.items() if k not in _ATOMIC_RESERVED}
    return CheckConfig(
        name=name,
        command=str(command),
        parser=str(parser),
        timeout=timeout,
        options=options,
    )


def _validate_aliases(checks: dict[str, CheckConfig]) -> None:
    for name, check in checks.items():
        if not check.is_alias:
            continue
        assert check.members is not None
        seen: set[str] = set()
        for member in check.members:
            if member == name:
                raise ConfigError(
                    f"[check.{name}] members must not include itself"
                )
            if member in seen:
                raise ConfigError(
                    f"[check.{name}] members lists '{member}' more than once"
                )
            seen.add(member)
            target = checks.get(member)
            if target is None:
                raise ConfigError(
                    f"[check.{name}] members references unknown check "
                    f"'{member}'"
                )
            if target.is_alias:
                raise ConfigError(
                    f"[check.{name}] members must be atomic checks; "
                    f"'{member}' is an alias (nesting is not supported)"
                )


def load_config(path: Path | None = None) -> Config:
    cfg_path = (path or Path.cwd() / CONFIG_NAME).resolve()
    if not cfg_path.exists():
        raise ConfigError(
            f"config not found: {cfg_path} (run `ckdn init` to create a starter config)"
        )
    try:
        data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {cfg_path}: {exc}") from exc

    run_raw = data.get("run", {})
    if not isinstance(run_raw, dict):
        raise ConfigError("[run] must be a table")
    run = RunSettings(
        runs_dir=Path(str(run_raw.get("runs_dir", ".agent-runs"))),
        keep=int(run_raw.get("keep", 20)),
        top=int(run_raw.get("top", 20)),
        max_snippet_lines=int(run_raw.get("max_snippet_lines", 12)),
        log_tail_lines=int(run_raw.get("log_tail_lines", 40)),
    )

    checks_raw = data.get("check", {})
    if not isinstance(checks_raw, dict) or not checks_raw:
        raise ConfigError("no [check.<name>] sections defined")

    checks: dict[str, CheckConfig] = {}
    for name, raw in checks_raw.items():
        if not isinstance(raw, dict):
            raise ConfigError(f"[check.{name}] must be a table")
        checks[name] = _parse_check(name, raw)

    _validate_aliases(checks)
    return Config(root=cfg_path.parent, run=run, checks=checks)
