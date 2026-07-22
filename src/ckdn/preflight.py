# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Pre-flight diagnostics for a configuration (``ckdn doctor``).

Static, deterministic checks that run *before* any subprocess and catch the
most common ways a check is misconfigured — a tool that is not on ``PATH``, or
a command whose flags do not match what its parser will look for. Catching
these up front turns a confusing runtime ``error`` ("report not found") into an
actionable message.

Diagnostics are advisory heuristics, not the status model: ``error`` marks a
run that cannot possibly work (missing executable); ``warning`` marks a likely
misconfiguration. Neither runs the command.
"""

from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass

from ckdn.config import CheckConfig, Config

#: File-based parsers read a report the command must write into ``{run_dir}``.
#: Maps parser name -> (artifact option key, default filename).
_REPORT_ARTIFACT: dict[str, tuple[str, str]] = {
    "pytest": ("junit", "junit.xml"),
    "coverage": ("coverage_xml", "coverage.xml"),
    "ruff": ("report", "ruff.json"),
    "bandit": ("report", "bandit.json"),
    "pip_audit": ("report", "pip-audit.json"),
    "pylint": ("report", "pylint.json"),
    "sarif": ("report", "report.sarif"),
}


@dataclass(frozen=True)
class Diagnostic:
    """One pre-flight finding about a check. ``level`` is ``error``/``warning``."""

    check: str
    level: str
    message: str


def _flag_diagnostic(check: CheckConfig) -> str | None:
    """Warn when a log parser's required flag is absent from the command."""
    command = check.command or ""
    parser = check.parser
    fmt = str(check.options.get("format", "text"))
    if (
        parser == "mypy"
        and fmt == "json"
        and "--output json" not in command
        and "--output=json" not in command
    ):
        return (
            'mypy is in json mode (format = "json") but the command has '
            "no `--output json`"
        )
    if parser == "pyright" and "--outputjson" not in command:
        return "pyright parser expects `--outputjson` in the command"
    if parser == "reformat" and "--check" not in command:
        return "reformat parser expects `--check` (and no `--diff`)"
    return None


def _tokenize(command: str) -> list[str] | None:
    try:
        return shlex.split(command)
    except ValueError:
        return None


def diagnose(cfg: Config) -> list[Diagnostic]:
    """Return pre-flight diagnostics for every atomic check in ``cfg``.

    Aliases are skipped (their members are checked individually). Results are
    ordered by config order, ``error`` before ``warning`` within a check.
    """
    out: list[Diagnostic] = []
    for name, check in cfg.checks.items():
        if check.is_alias:
            continue
        command = check.command or ""

        tokens = _tokenize(command)
        if tokens is None:
            out.append(
                Diagnostic(name, "error", f"command is not tokenizable: {command!r}")
            )
            continue
        if not tokens:
            out.append(Diagnostic(name, "error", "command is empty"))
            continue

        if shutil.which(tokens[0]) is None:
            out.append(
                Diagnostic(name, "error", f"executable not found on PATH: {tokens[0]}")
            )

        artifact = _REPORT_ARTIFACT.get(check.parser or "")
        if artifact is not None:
            key, default = artifact
            filename = str(check.options.get(key, default)).rsplit("/", 1)[-1]
            if filename not in command:
                out.append(
                    Diagnostic(
                        name,
                        "warning",
                        f"the {check.parser} parser reads `{filename}` from the run "
                        f"dir, but the command never writes it — add the flag that "
                        f"emits `{{run_dir}}/{filename}`",
                    )
                )

        flag = _flag_diagnostic(check)
        if flag is not None:
            out.append(Diagnostic(name, "warning", flag))
    return out
