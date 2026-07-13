# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""pre-commit parser over the default ``pre-commit run`` terminal output.

pre-commit does not emit a stable machine-readable report, so this parser
reads the hook summary lines and loud-failure metadata that ``pre-commit
run`` prints for each hook. Each failed hook becomes one finding; passed and
skipped hooks are counted in ``summary`` only.

Expected command shapes::

    uv run pre-commit run --all-files
    uv run pre-commit run --all-files --hook-stage pre-push
    uv run pre-commit run ruff-check --all-files

Guards:

1. A nonzero exit code with zero parsed failed hooks flips ``parser_ok`` off.
2. A zero exit code with parsed failed hooks is left to the reconciler as
   ``parse_mismatch``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ckdn.parsers.base import Finding, ParseContext, ParseResult, clamp

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_RESULT_SUFFIXES = ("Passed", "Failed", "Skipped")
_META_HOOK_ID_RE = re.compile(r"^- hook id:\s*(?P<hook_id>.+)$")
_META_EXIT_CODE_RE = re.compile(r"^- exit code:\s*(?P<rc>\d+)$")
_META_DURATION_RE = re.compile(r"^- duration:\s*(?P<duration>.+)$")
_META_MODIFIED_RE = re.compile(r"^- files were modified by this hook$")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _parse_header(line: str) -> tuple[str, str, str | None] | None:
    """Return ``(name, result, skip_reason)`` or ``None``."""
    cleaned = _strip_ansi(line).rstrip()
    for result in _RESULT_SUFFIXES:
        if not cleaned.endswith(result):
            continue
        body = cleaned[: -len(result)]
        skip_reason: str | None = None
        if result == "Skipped" and body.endswith(")"):
            open_paren = body.rfind("(")
            if open_paren != -1:
                skip_reason = body[open_paren + 1 : -1].strip() or None
                body = body[:open_paren]
        name = body.rstrip(".").strip()
        if not name:
            return None
        return name, result, skip_reason
    return None


@dataclass
class _HookRun:
    name: str
    result: str
    hook_id: str | None = None
    exit_code: int | None = None
    duration: str | None = None
    files_modified: bool = False
    skip_reason: str | None = None
    output: list[str] = field(default_factory=list)

    @property
    def finding_id(self) -> str:
        return self.hook_id or self.name


class PreCommitTextParser:
    name = "pre_commit"

    def parse(self, ctx: ParseContext) -> ParseResult:
        hooks = _scan_hooks(ctx.log_text)
        failed = [hook for hook in hooks if hook.result == "Failed"]
        findings = [
            Finding(
                id=hook.finding_id,
                kind="hook_failure",
                message=_failure_message(hook),
                detail=tuple(clamp(hook.output, ctx.max_snippet_lines)),
            )
            for hook in failed
        ]
        summary = {
            "hooks_total": len(hooks),
            "passed": sum(1 for hook in hooks if hook.result == "Passed"),
            "failed": len(failed),
            "skipped": sum(1 for hook in hooks if hook.result == "Skipped"),
            "failed_hooks": [hook.finding_id for hook in failed],
        }
        result = ParseResult(findings=findings, summary=summary)
        self._verify(ctx, result, len(failed), len(hooks))
        return result

    @staticmethod
    def _verify(
        ctx: ParseContext,
        result: ParseResult,
        failed_count: int,
        hooks_total: int,
    ) -> None:
        if hooks_total == 0 and ctx.log_text.strip():
            result.parser_ok = False
            result.notes.append(
                "pre-commit output is present but no hook summary lines were "
                "parsed; unexpected format"
            )
            return
        if ctx.rc != 0 and failed_count == 0:
            result.parser_ok = False
            result.notes.append(
                "pre-commit exited nonzero but no failed hooks were parsed; "
                "inspect log_tail"
            )


def _failure_message(hook: _HookRun) -> str:
    if hook.files_modified:
        return f"{hook.name} modified files"
    if hook.exit_code is not None:
        return f"{hook.name} failed (exit code {hook.exit_code})"
    return f"{hook.name} failed"


def _scan_hooks(log_text: str) -> list[_HookRun]:
    lines = [_strip_ansi(line) for line in log_text.splitlines()]
    hooks: list[_HookRun] = []
    index = 0
    while index < len(lines):
        header = _parse_header(lines[index])
        if header is None:
            index += 1
            continue
        name, result, skip_reason = header
        index += 1
        hook_id: str | None = None
        exit_code: int | None = None
        duration: str | None = None
        files_modified = False
        while index < len(lines) and lines[index].startswith("- "):
            meta_line = lines[index].strip()
            hook_match = _META_HOOK_ID_RE.match(meta_line)
            if hook_match is not None:
                hook_id = hook_match.group("hook_id").strip()
            exit_match = _META_EXIT_CODE_RE.match(meta_line)
            if exit_match is not None:
                exit_code = int(exit_match.group("rc"))
            duration_match = _META_DURATION_RE.match(meta_line)
            if duration_match is not None:
                duration = duration_match.group("duration").strip()
            if _META_MODIFIED_RE.match(meta_line) is not None:
                files_modified = True
            index += 1
        output: list[str] = []
        while index < len(lines):
            if _parse_header(lines[index]) is not None:
                break
            if lines[index].strip():
                output.append(lines[index].rstrip())
            index += 1
        hooks.append(
            _HookRun(
                name=name,
                result=result,
                hook_id=hook_id,
                exit_code=exit_code,
                duration=duration,
                files_modified=files_modified,
                skip_reason=skip_reason,
                output=output,
            )
        )
    return hooks
