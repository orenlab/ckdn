# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Parser contract shared by built-in and custom parsers.

A parser never decides the final status. It reports *facts*:

* ``findings``      -- structured failure evidence extracted from tool output
* ``summary``       -- bounded machine-readable metrics (counts, percentages)
* ``gate_failures`` -- config-level gates that did not hold (e.g. coverage
                       below ``fail_under``), regardless of the exit code
* ``parser_ok``     -- whether the parser is confident it understood the
                       output. ``False`` means "do not trust anything here",
                       and the reconciler will never report green.

The final status is derived by :mod:`ckdn.reconcile` from the process
exit code plus the ParseResult. This split is the core safety property of
ckdn: text is never allowed to override the exit code, and the exit code
is never allowed to look green when the parser disagrees.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class ArtifactPathError(ValueError):
    """Parser artifact path escapes the run directory or cannot be resolved."""


@dataclass(frozen=True)
class Finding:
    """One unit of failure evidence (a failed test, a type error, a lint hit)."""

    id: str
    kind: str
    message: str
    location: str | None = None
    detail: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "message": self.message,
        }
        if self.location is not None:
            out["location"] = self.location
        if self.detail:
            out["detail"] = list(self.detail)
        return out


@dataclass
class ParseResult:
    """Facts extracted from one tool run. See module docstring for semantics."""

    parser_ok: bool = True
    findings: list[Finding] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    gate_failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    #: Whether a nonzero exit code is expected to come with findings.
    #: True for pytest/ty/ruff (a failure without evidence is an infra
    #: error). False for the generic parser, which never has findings.
    evidence_expected: bool = True

    #: Parser may request the log tail in the digest even on plain failures
    #: (the generic parser does this, since the tail is its only evidence).
    include_log_tail: bool = False


@dataclass(frozen=True)
class ParseContext:
    """Everything a parser is allowed to look at."""

    run_dir: Path
    log_text: str
    rc: int
    options: Mapping[str, Any]
    top: int
    max_snippet_lines: int

    def artifact(self, key: str, default: str) -> Path:
        """Resolve an artifact path from parser options."""
        raw = str(self.options.get(key, default))
        return artifact_path(self.run_dir, raw)


def resolve_under_run_dir(run_dir: Path, candidate: Path) -> Path:
    """Resolve ``candidate`` and require the result stays inside ``run_dir``.

    Follows symlinks; any target outside the run directory (including
    ``/etc/passwd``-style absolute paths) raises :class:`ArtifactPathError`.
    """
    root = run_dir.resolve()
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise ArtifactPathError(
            f"artifact path {candidate!s} could not be resolved: {exc}"
        ) from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ArtifactPathError(
            f"artifact path {candidate!s} escapes run directory {run_dir}"
        ) from exc
    return resolved


def artifact_path(run_dir: Path, raw: str) -> Path:
    """Resolve a parser artifact template strictly under ``run_dir``.

    ``{run_dir}`` is substituted first. Relative paths anchor under
    ``run_dir``; absolute paths are accepted only when ``resolve()`` keeps
    them inside ``run_dir`` (so ``/etc/passwd`` and ``..`` escapes are
    rejected before any read).
    """
    substituted = raw.replace("{run_dir}", str(run_dir))
    path = Path(substituted)
    candidate = path if path.is_absolute() else run_dir / path
    return resolve_under_run_dir(run_dir, candidate)


class Parser(Protocol):
    """Structural type every parser must satisfy."""

    name: str

    def parse(self, ctx: ParseContext) -> ParseResult: ...


def clamp(lines: list[str], limit: int) -> list[str]:
    """Bound a list of lines, appending an explicit truncation marker."""
    if limit <= 0:
        return []
    if len(lines) <= limit:
        return lines
    return [*lines[:limit], f"... truncated {len(lines) - limit} more lines"]


def top_counts(counts: dict[str, int], top: int) -> dict[str, int]:
    """Return the top ``top`` keys by count (ties broken by key)."""
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return dict(items[:top] if top > 0 else items)


def format_location(
    path: object,
    line: object = None,
    column: object = None,
    *,
    default_path: str = "?",
) -> str:
    """Build ``path``, ``path:line``, or ``path:line:column`` from parts."""
    path_s = str(path or default_path)
    if line is None:
        return path_s
    if column is None:
        return f"{path_s}:{line}"
    return f"{path_s}:{line}:{column}"


def _json_fail(note: str) -> tuple[None, ParseResult]:
    return None, ParseResult(parser_ok=False, notes=[note])


def _shaped_json(
    data: Any,
    *,
    expect: type,
    tool: str,
    nested_key: str | None,
    nested_expect: type | None,
    nested_missing_note: str | None,
) -> tuple[Any, str | None]:
    """Return ``(payload, None)`` or ``(None, fail_note)`` for shape checks."""
    fail: str | None = None
    if not isinstance(data, expect):
        kind = "object" if expect is dict else "array"
        fail = f"{tool} report is not a JSON {kind}; unexpected format"
    elif nested_key is None:
        return data, None
    else:
        nested = data.get(nested_key) if isinstance(data, dict) else None
        if nested_expect is not None and not isinstance(nested, nested_expect):
            fail = nested_missing_note or (
                f"{tool} report missing `{nested_key}`; unexpected format"
            )
        else:
            return (data, nested), None
    return None, fail


def load_json_artifact(
    ctx: ParseContext,
    *,
    default_name: str,
    tool: str,
    expect: type,
    missing_hint: str,
    key: str = "report",
    nested_key: str | None = None,
    nested_expect: type | None = None,
    nested_missing_note: str | None = None,
) -> tuple[Any, ParseResult | None]:
    """Load a JSON report from the run directory.

    Returns ``(data, None)`` on success, or ``(None, error_result)`` when the
    file is missing, invalid JSON, or not of the expected top-level type.
    When ``nested_key`` is set, returns ``((root, nested), None)``.
    """
    report_path = ctx.artifact(key, default_name)
    if not report_path.exists():
        return _json_fail(
            f"{tool} JSON report not found: {report_path}. {missing_hint}"
        )
    try:
        data = json.loads(report_path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        return _json_fail(f"{tool} report is not valid JSON: {exc}")
    payload, fail_note = _shaped_json(
        data,
        expect=expect,
        tool=tool,
        nested_key=nested_key,
        nested_expect=nested_expect,
        nested_missing_note=nested_missing_note,
    )
    if fail_note is not None:
        return _json_fail(fail_note)
    return payload, None
