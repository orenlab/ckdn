# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Run atomic checks and aliases (shared by CLI and MCP)."""

from __future__ import annotations

import datetime as dt

from ckdn.app.errors import (
    AliasExtraArgsError,
    NotAliasError,
    NotAtomicError,
    UnknownCheckError,
    UnknownParserError,
)
from ckdn.app.types import AliasRunResult, AtomicRunResult
from ckdn.command_policy import CommandPolicyError, validate_command
from ckdn.config import CheckConfig, Config
from ckdn.digest import (
    META_NAME,
    build_alias_aggregate,
    build_digest,
    build_meta,
    dump_json,
    list_artifacts,
    write_documents,
)
from ckdn.parsers import available_parsers, get_parser
from ckdn.parsers.base import ParseContext, ParseResult
from ckdn.reconcile import reconcile
from ckdn.runner import (
    LOG_NAME,
    RC_POLICY,
    RunOutcome,
    build_tokens,
    create_run_dir,
    execute,
    prune,
    update_latest,
)


def exit_from_outcome(rc: int, status: str) -> int:
    if rc != 0:
        return rc if 0 < rc <= 255 else 1
    return 0 if status == "pass" else 1


def run_one(
    cfg: Config,
    check: CheckConfig,
    *,
    extra: list[str] | None = None,
) -> AtomicRunResult:
    """Run one atomic check and persist digest/meta under the run directory."""
    if check.is_alias or check.command is None or check.parser is None:
        raise NotAtomicError(f"[check.{check.name}] is not an atomic check")

    parser = get_parser(check.parser)
    if parser is None:
        raise UnknownParserError(
            f"[check.{check.name}] uses unknown parser '{check.parser}'; "
            "available: " + ", ".join(available_parsers())
        )

    run_dir = create_run_dir(cfg.runs_dir, check.name)
    tokens = build_tokens(check.command, run_dir, list(extra or ()))
    policy_blocked = False
    try:
        validate_command(
            check.command,
            list(extra or ()),
            cwd=cfg.cwd,
            policy=cfg.run.command_policy,
            allowlist_prefixes=cfg.run.command_allowlist,
            tokens=tokens,
        )
    except CommandPolicyError as exc:
        policy_blocked = True
        outcome = RunOutcome(
            run_dir=run_dir,
            tokens=tokens,
            rc=RC_POLICY,
            log_text="",
            started_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            duration_s=0.0,
            timed_out=False,
            exec_note=str(exc),
        )
        (run_dir / LOG_NAME).write_text("", encoding="utf-8")
        result = ParseResult(
            parser_ok=False,
            notes=[str(exc)],
            evidence_expected=True,
            include_log_tail=True,
        )
    else:
        outcome = execute(tokens, cwd=cfg.cwd, run_dir=run_dir, timeout=check.timeout)

    if not policy_blocked:
        try:
            result = parser.parse(
                ParseContext(
                    run_dir=run_dir,
                    log_text=outcome.log_text,
                    rc=outcome.rc,
                    options=check.options,
                    top=int(check.options.get("top", cfg.run.top)),
                    max_snippet_lines=cfg.run.max_snippet_lines,
                )
            )
        except Exception as exc:  # a parser bug must never hide a result
            result = ParseResult(
                parser_ok=False,
                notes=[f"parser '{check.parser}' crashed: {exc!r}"],
            )
        if outcome.exec_note:
            result.notes.insert(0, outcome.exec_note)

    status, reason, include_tail = reconcile(outcome.rc, result)

    meta = build_meta(check=check.name, parser=check.parser, outcome=outcome)
    (run_dir / META_NAME).write_text(dump_json(meta), encoding="utf-8")
    try:
        run_dir_rel = str(run_dir.relative_to(cfg.cwd))
    except ValueError:
        run_dir_rel = str(run_dir)
    digest = build_digest(
        check=check.name,
        status=status,
        reason=reason,
        outcome=outcome,
        result=result,
        run_dir_rel=run_dir_rel,
        top=int(check.options.get("top", cfg.run.top)),
        include_tail=include_tail,
        tail_lines=cfg.run.log_tail_lines,
        artifacts=list_artifacts(run_dir),
    )
    write_documents(run_dir, digest, meta)
    update_latest(cfg.runs_dir, run_dir)
    prune(cfg.runs_dir, cfg.run.keep)

    return AtomicRunResult(
        check=check.name,
        status=status,
        rc=outcome.rc,
        run_dir=run_dir,
        digest=digest,
        exit_code=exit_from_outcome(outcome.rc, status),
    )


def _alias_aggregate_exit(results: list[AtomicRunResult]) -> int:
    for item in results:
        if item.rc != 0:
            return item.rc if 0 < item.rc <= 255 else 1
    if any(item.status != "pass" for item in results):
        return 1
    return 0


def run_alias(cfg: Config, alias: CheckConfig) -> AliasRunResult:
    """Run an alias's members in order; return aggregate + member results."""
    if not alias.is_alias or alias.members is None:
        raise NotAliasError(f"[check.{alias.name}] is not an alias")

    results: list[AtomicRunResult] = []
    for member_name in alias.members:
        member = cfg.checks[member_name]
        outcome = run_one(cfg, member, extra=[])
        results.append(outcome)
        if alias.fail_fast and outcome.exit_code != 0:
            break

    exit_code = _alias_aggregate_exit(results)
    status = "pass" if exit_code == 0 else "fail"
    aggregate = build_alias_aggregate(
        alias=alias.name,
        results=[(r.check, r.status, r.rc, r.run_dir) for r in results],
        status=status,
        rc=exit_code,
    )
    return AliasRunResult(
        alias=alias.name,
        status=status,
        aggregate=aggregate,
        members=tuple(results),
        exit_code=exit_code,
    )


def run_check(
    cfg: Config,
    name: str,
    *,
    extra: list[str] | None = None,
) -> AtomicRunResult | AliasRunResult:
    """Dispatch by check kind. Aliases reject ``extra``."""
    check = cfg.checks.get(name)
    if check is None:
        raise UnknownCheckError(
            f"unknown check '{name}'; configured: " + ", ".join(sorted(cfg.checks))
        )
    extra_args = list(extra or ())
    if check.is_alias:
        if extra_args:
            raise AliasExtraArgsError(
                f"alias '{check.name}' does not accept extra arguments; "
                "run an atomic member check instead "
                f"(members: {', '.join(check.members or ())})"
            )
        return run_alias(cfg, check)
    return run_one(cfg, check, extra=extra_args)
