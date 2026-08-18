# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Command argv policy: workspace containment, sensitive-path denylist, allowlist."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

CommandPolicy = Literal["workspace", "allowlist", "off"]

COMMAND_POLICIES: frozenset[str] = frozenset({"workspace", "allowlist", "off"})

# Starter-compatible defaults. ``[run.command_allowlist].prefixes``
# **replaces** this set rather than extending it -- see
# ``effective_allowlist_prefixes``.
DEFAULT_ALLOWLIST_PREFIXES: tuple[str, ...] = (
    "uv run ",
    "uvx ",
    "true",
    "false",
)

_SENSITIVE_ROOTS: tuple[Path, ...] = (
    Path("/etc"),
    Path("/proc"),
    Path("/sys"),
    Path("/dev"),
)

_HOME_SECRET_DIRS: tuple[str, ...] = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".netrc",
    ".docker",
    ".kube",
)


class CommandPolicyError(ValueError):
    """Configured command or argv violates the active command policy."""


def effective_allowlist_prefixes(
    custom: tuple[str, ...] | None,
) -> tuple[str, ...]:
    return custom if custom is not None else DEFAULT_ALLOWLIST_PREFIXES


def command_matches_allowlist(command: str, prefixes: tuple[str, ...]) -> bool:
    """Return whether ``command`` starts with an allowed prefix or exact entry."""
    cmd = command.strip()
    if not cmd:
        return False
    for prefix in prefixes:
        if prefix.endswith(" "):
            if cmd.startswith(prefix):
                return True
        elif cmd == prefix or cmd.startswith(f"{prefix} "):
            return True
    return False


def _is_sensitive_path(resolved: Path) -> bool:
    # `is_relative_to` answers with a bool on every platform -- a foreign drive
    # letter is simply False -- so there is nothing here to catch.
    if any(resolved.is_relative_to(root) for root in _SENSITIVE_ROOTS):
        return True
    try:
        home = Path.home().resolve()
    except RuntimeError:
        return False
    return any(
        resolved.is_relative_to((home / name).resolve()) for name in _HOME_SECRET_DIRS
    )


def _path_like_segments(value: str) -> list[str]:
    """Extract path-like fragments from one argv token."""
    if not value:
        return []
    if value.startswith("-") and "=" in value:
        return _path_like_segments(value.split("=", 1)[1])
    if value.startswith("-"):
        return []
    if ":" in value and not value.startswith("/") and not value.startswith("."):
        _scheme, sep, tail = value.partition(":")
        if sep and tail and (tail.startswith(("/", ".", "~")) or "/" in tail):
            return _path_like_segments(tail)
    return [value]


def _looks_like_path(segment: str) -> bool:
    if not segment:
        return False
    if segment.startswith(("/", "~")):
        return True
    if segment in {".", ".."}:
        return True
    return "/" in segment or "\\" in segment


def _resolve_workspace_path(segment: str, cwd: Path) -> Path:
    expanded = Path(segment).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (cwd / expanded).resolve()


def _validate_path_segment(segment: str, cwd: Path) -> None:
    if not _looks_like_path(segment):
        return
    resolved = _resolve_workspace_path(segment, cwd)
    root = cwd.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise CommandPolicyError(
            f"command path escapes workspace ({root}): {segment!r}"
        ) from exc
    if _is_sensitive_path(resolved):
        raise CommandPolicyError(
            f"command path targets a sensitive location: {segment!r}"
        )


def validate_command_tokens(
    tokens: list[str],
    *,
    cwd: Path,
    policy: CommandPolicy,
) -> None:
    """Enforce workspace containment and sensitive-path rules on argv tokens."""
    if policy != "workspace":
        return
    root = cwd.resolve()
    for token in tokens:
        for segment in _path_like_segments(token):
            _validate_path_segment(segment, root)


def validate_command(
    command: str,
    *,
    cwd: Path,
    policy: CommandPolicy,
    tokens: list[str],
    allowlist_prefixes: tuple[str, ...] | None = None,
) -> list[str]:
    """Validate one invocation and return its argv token list.

    ``command`` is the configured string, matched against the allowlist;
    ``tokens`` is the argv it was built into -- the configured command plus any
    extra arguments, after ``{run_dir}`` substitution -- and is what the path
    rules actually inspect. Only the caller can build it: substitution needs a
    run directory this module knows nothing about. Raises
    :class:`CommandPolicyError` when the active policy rejects the invocation.
    """
    argv = list(tokens)
    if policy == "off":
        return argv

    if policy == "allowlist":
        prefixes = effective_allowlist_prefixes(allowlist_prefixes)
        if not command_matches_allowlist(command, prefixes):
            raise CommandPolicyError(
                "command does not match any configured allowlist prefix; "
                f"allowed prefixes: {', '.join(repr(p) for p in prefixes)}"
            )

    # Path arguments are confined under either policy: an allowed executable
    # is still not allowed to read outside the workspace.
    validate_command_tokens(argv, cwd=cwd, policy="workspace")
    return argv
