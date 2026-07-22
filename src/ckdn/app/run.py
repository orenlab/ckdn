# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Run atomic checks and aliases (shared by CLI and MCP)."""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import signal
from collections.abc import Callable, Iterable, Iterator
from typing import Any, TypeVar

from ckdn import baseline
from ckdn.app.errors import (
    AliasExtraArgsError,
    AppError,
    NotAliasError,
    NotAtomicError,
    UnknownCheckError,
    UnknownParserError,
)
from ckdn.app.types import AliasRunResult, AtomicRunResult
from ckdn.command_policy import CommandPolicyError, validate_command
from ckdn.config import CheckConfig, Config
from ckdn.digest import (
    build_alias_aggregate,
    build_digest,
    build_meta,
    list_artifacts,
    write_documents,
)
from ckdn.parsers import available_parsers, get_parser
from ckdn.parsers.base import ParseContext, Parser, ParseResult
from ckdn.reconcile import reconcile
from ckdn.runner import (
    LOG_NAME,
    RC_INTERRUPTED,
    RC_POLICY,
    RunLockError,
    RunOutcome,
    build_tokens,
    create_run_dir,
    execute,
    prune,
    run_lock,
    update_latest,
)

_T = TypeVar("_T")


def exit_from_outcome(rc: int, status: str) -> int:
    if rc != 0:
        return rc if 0 < rc <= 255 else 1
    return 0 if status == "pass" else 1


def _annotate_baseline(
    cfg: Config,
    check_name: str,
    execution_status: str,
    result: ParseResult,
    digest: dict[str, Any],
) -> None:
    """Classify findings against the baseline and attach ``baseline``/``gate``.

    Execution truth (``digest['status']``) is never touched — see
    :mod:`ckdn.baseline`. Only runs when ``[run].baseline`` is configured.
    """
    baseline_path = cfg.baseline_path
    if baseline_path is None:
        return
    accepted = baseline.load(baseline_path).get(check_name, set())
    new = 0
    known = 0
    for finding in result.findings:
        if baseline.fingerprint(check_name, finding.to_dict()) in accepted:
            known += 1
        else:
            new += 1
    for shown in digest.get("findings", []):
        if baseline.fingerprint(check_name, shown) in accepted:
            shown["baselined"] = True
    if known or new:
        digest["baseline"] = {"known": known, "new": new}
    digest["gate"] = baseline.gate(execution_status, result.parser_ok, new)


@contextlib.contextmanager
def _sigint_held() -> Iterator[None]:
    """Hold off Ctrl-C for a moment; a worker thread simply cannot."""
    try:
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    except ValueError:  # not the main thread — the MCP server runs there
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def _uninterruptible(step: Callable[[], _T]) -> _T:
    """Produce the run's evidence even while Ctrl-C is being held down.

    An empty run directory is the exact symptom of the incident this whole
    path exists to prevent, so the retry runs with SIGINT held off rather
    than racing another keypress. Nothing is swallowed that matters: the
    outcome already records the interrupt, and the run exits 130 either way.

    ``step`` must be idempotent — it is rebuilt from the same inputs and the
    documents it writes are overwritten wholesale.
    """
    try:
        return step()
    except KeyboardInterrupt:
        with _sigint_held():
            return step()


def _run_sequence(
    cfg: Config, checks: Iterable[CheckConfig], *, fail_fast: bool
) -> list[AtomicRunResult]:
    """Run checks in order, stopping early when the sequence must not continue.

    A run cut short by Ctrl-C always ends the sequence; ``fail_fast`` also
    stops at the first non-green member.
    """
    results: list[AtomicRunResult] = []
    for check in checks:
        outcome = run_one(cfg, check, extra=[])
        results.append(outcome)
        if outcome.digest.get("interrupted") or (fail_fast and outcome.exit_code != 0):
            break
    return results


def _attach_aggregate_gate(
    aggregate: dict[str, Any], results: list[AtomicRunResult]
) -> None:
    """Combine member gates into an aggregate gate (unavailable > fail > pass)."""
    combined = baseline.combine_gate([r.digest for r in results])
    if combined is not None:
        aggregate["gate"] = combined


def run_one(
    cfg: Config,
    check: CheckConfig,
    *,
    extra: list[str] | None = None,
) -> AtomicRunResult:
    """Run one atomic check and persist digest/meta under the run directory.

    Serialized per ``(runs_dir, check)``: a second concurrent run of the same
    check is refused instead of doubling the load on the same tools.
    """
    if check.is_alias or check.command is None or check.parser is None:
        raise NotAtomicError(f"[check.{check.name}] is not an atomic check")

    parser = get_parser(check.parser)
    if parser is None:
        raise UnknownParserError(
            f"[check.{check.name}] uses unknown parser '{check.parser}'; "
            "available: " + ", ".join(available_parsers())
        )

    try:
        with run_lock(cfg.runs_dir, check.name) as lock_note:
            return _run_atomic(
                cfg, check, check.command, parser, list(extra or ()), lock_note
            )
    except RunLockError as exc:
        raise AppError(str(exc)) from exc


def _run_atomic(
    cfg: Config,
    check: CheckConfig,
    command: str,
    parser: Parser,
    extra: list[str],
    lock_note: str | None = None,
) -> AtomicRunResult:
    """Execute one already-validated atomic check while holding its lock."""
    run_dir = create_run_dir(cfg.runs_dir, check.name)
    tokens = build_tokens(command, run_dir, extra)
    policy_blocked = False
    try:
        validate_command(
            command,
            extra,
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
        run_env = {
            key: value.replace("{run_dir}", str(run_dir))
            for key, value in check.env.items()
        } or None
        outcome = execute(
            tokens,
            cwd=cfg.cwd,
            run_dir=run_dir,
            timeout=check.timeout,
            env=run_env,
        )

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
        except KeyboardInterrupt:
            # Ctrl-C after the command finished but before its output was
            # understood. The command's own exit code stops being the run's
            # outcome the moment the run is cut short: a Ctrl-C is 130
            # everywhere else, and leaving the tool's code here would make the
            # digest contradict the status model on a real interrupt path.
            # It is not lost — the note keeps it.
            command_rc = outcome.rc
            outcome = dataclasses.replace(outcome, interrupted=True, rc=RC_INTERRUPTED)
            result = ParseResult(
                parser_ok=False,
                notes=[
                    "run interrupted while parsing the tool output; the "
                    f"command itself had exited {command_rc}"
                ],
                evidence_expected=True,
                include_log_tail=True,
            )
        except Exception as exc:  # a parser bug must never hide a result
            result = ParseResult(
                parser_ok=False,
                notes=[f"parser '{check.parser}' crashed: {exc!r}"],
            )
        if outcome.exec_note:
            result.notes.insert(0, outcome.exec_note)

    if lock_note:
        # Advisory only: a reclaimed lock says nothing about *this* run's
        # outcome, so it is recorded as evidence and never touches the status.
        result.notes.insert(0, lock_note)

    try:
        # as_posix keeps the digest path separator stable across OSes, so a
        # digest generated on Windows is byte-identical to one on POSIX.
        run_dir_rel = run_dir.relative_to(cfg.cwd).as_posix()
    except ValueError:
        run_dir_rel = run_dir.as_posix()

    def _finalize() -> tuple[str, dict[str, Any]]:
        # Building the documents belongs inside the protected step, not just
        # writing them: a Ctrl-C in reconcile or build_digest would otherwise
        # abandon a run directory that has a log but no digest — the same
        # symptom, one stage earlier.
        status, reason, include_tail = reconcile(
            outcome.rc,
            result,
            interrupted=outcome.interrupted,
            timed_out=outcome.timed_out,
        )
        meta = build_meta(check=check.name, parser=parser.name, outcome=outcome)
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
        _annotate_baseline(cfg, check.name, status, result, digest)
        write_documents(run_dir, digest, meta)
        update_latest(cfg.runs_dir, run_dir)
        prune(cfg.runs_dir, cfg.run.keep)
        return status, digest

    status, digest = _uninterruptible(_finalize)

    return AtomicRunResult(
        check=check.name,
        status=status,
        rc=outcome.rc,
        run_dir=run_dir,
        digest=digest,
        exit_code=exit_from_outcome(outcome.rc, status),
    )


def _sequence_interrupted(results: list[AtomicRunResult]) -> bool:
    return any(item.digest.get("interrupted") for item in results)


def _alias_aggregate_exit(results: list[AtomicRunResult]) -> int:
    # Interruption outranks the members' own codes. Otherwise an early red
    # member wins the pass-through and the series reports its verdict, hiding
    # that the rest never ran: `ruff` fails, Ctrl-C stops `pytest`, and the
    # alias exits 1 as though it had simply found problems.
    if _sequence_interrupted(results):
        return RC_INTERRUPTED
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

    results = _run_sequence(
        cfg,
        (cfg.checks[name] for name in alias.members),
        fail_fast=alias.fail_fast,
    )

    exit_code = _alias_aggregate_exit(results)
    status = "pass" if exit_code == 0 else "fail"
    aggregate = build_alias_aggregate(
        interrupted=_sequence_interrupted(results),
        alias=alias.name,
        # r.digest["run_dir"] is the member's own relative, posix run dir, so
        # the aggregate and the member digest report identical paths.
        results=[(r.check, r.status, r.rc, r.digest["run_dir"]) for r in results],
        status=status,
        rc=exit_code,
    )
    _attach_aggregate_gate(aggregate, results)
    return AliasRunResult(
        alias=alias.name,
        status=status,
        aggregate=aggregate,
        members=tuple(results),
        exit_code=exit_code,
    )


def run_all(cfg: Config, *, fail_fast: bool = False) -> AliasRunResult:
    """Run every **atomic** check in config order and return one aggregate.

    Aliases are skipped (they only group atomics, which all run here anyway).
    Defaults to running every check; ``fail_fast`` stops at the first non-green
    one. The aggregate uses ``alias = "*"`` to denote "all atomic checks".
    """
    results = _run_sequence(
        cfg,
        (check for check in cfg.checks.values() if not check.is_alias),
        fail_fast=fail_fast,
    )

    exit_code = _alias_aggregate_exit(results)
    status = "pass" if exit_code == 0 else "fail"
    aggregate = build_alias_aggregate(
        interrupted=_sequence_interrupted(results),
        alias="*",
        results=[(r.check, r.status, r.rc, r.digest["run_dir"]) for r in results],
        status=status,
        rc=exit_code,
    )
    _attach_aggregate_gate(aggregate, results)
    return AliasRunResult(
        alias="*",
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
