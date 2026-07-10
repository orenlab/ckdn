# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""ckdn command-line interface.

Exit code contract of ``ckdn run``:

* the original command's nonzero exit code is passed through (clamped to
  1..255), so hooks and CI behave exactly as if the raw command had run;
* rc == 0 with a non-green status (``parse_mismatch``, or a gate failure)
  exits 1 -- ckdn may downgrade green, never upgrade red.

Aliases (``members = [...]``) run each atomic member in order. Each member
gets its own run directory and digest. Exit code: first nonzero tool rc if
any, else 1 if any member status is not ``pass``, else 0.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ckdn import __version__
from ckdn.config import (
    CONFIG_NAME,
    STARTER_CONFIG,
    CheckConfig,
    Config,
    ConfigError,
    load_config,
)
from ckdn.digest import (
    DIGEST_NAME,
    META_NAME,
    build_alias_aggregate,
    build_digest,
    build_meta,
    dump_json,
    dump_json_pretty,
    list_artifacts,
    write_documents,
)
from ckdn.parsers import available_parsers, get_parser
from ckdn.parsers.base import ParseContext, ParseResult
from ckdn.reconcile import reconcile
from ckdn.runner import (
    build_tokens,
    create_run_dir,
    execute,
    list_run_dirs,
    prune,
    resolve_run_dir,
    update_latest,
)


def _fail(message: str) -> int:
    print(f"ckdn: {message}", file=sys.stderr)
    return 2


def _load(args: argparse.Namespace) -> Config:
    return load_config(Path(args.config) if args.config else None)


def _exit_from_outcome(rc: int, status: str) -> int:
    if rc != 0:
        return rc if 0 < rc <= 255 else 1
    return 0 if status == "pass" else 1


@dataclass(frozen=True)
class AtomicRunResult:
    check: str
    status: str
    rc: int
    run_dir: Path
    digest: dict[str, Any]
    exit_code: int


def run_one(
    cfg: Config,
    check: CheckConfig,
    *,
    extra: list[str],
    quiet: bool,
) -> AtomicRunResult | int:
    """Run one atomic check. Returns result, or an int error exit code."""
    if check.is_alias or check.command is None or check.parser is None:
        return _fail(f"[check.{check.name}] is not an atomic check")

    parser = get_parser(check.parser)
    if parser is None:
        return _fail(
            f"[check.{check.name}] uses unknown parser '{check.parser}'; "
            "available: " + ", ".join(available_parsers())
        )

    run_dir = create_run_dir(cfg.runs_dir, check.name)
    tokens = build_tokens(check.command, run_dir, extra)
    outcome = execute(tokens, cwd=cfg.root, run_dir=run_dir, timeout=check.timeout)

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
        run_dir_rel = str(run_dir.relative_to(cfg.root))
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

    if not quiet:
        print(dump_json(digest), end="")

    return AtomicRunResult(
        check=check.name,
        status=status,
        rc=outcome.rc,
        run_dir=run_dir,
        digest=digest,
        exit_code=_exit_from_outcome(outcome.rc, status),
    )


def _alias_aggregate_exit(results: list[AtomicRunResult]) -> int:
    for item in results:
        if item.rc != 0:
            return item.rc if 0 < item.rc <= 255 else 1
    if any(item.status != "pass" for item in results):
        return 1
    return 0


def _run_alias(
    cfg: Config,
    alias: CheckConfig,
    *,
    quiet: bool,
) -> int:
    assert alias.members is not None
    results: list[AtomicRunResult] = []
    for member_name in alias.members:
        member = cfg.checks[member_name]
        # Members always quiet on stdout; only the aggregate is printed.
        outcome = run_one(cfg, member, extra=[], quiet=True)
        if isinstance(outcome, int):
            return outcome
        results.append(outcome)
        if alias.fail_fast and outcome.exit_code != 0:
            break

    exit_code = _alias_aggregate_exit(results)
    aggregate = build_alias_aggregate(
        alias=alias.name,
        results=[(r.check, r.status, r.rc, r.run_dir) for r in results],
        status="pass" if exit_code == 0 else "fail",
    )
    if not quiet:
        print(dump_json(aggregate), end="")
    return exit_code


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _load(args)
    check = cfg.checks.get(args.check)
    if check is None:
        return _fail(
            f"unknown check '{args.check}'; configured: "
            + ", ".join(sorted(cfg.checks))
        )
    extra = list(args.extra)
    if check.is_alias:
        if extra:
            return _fail(
                f"alias '{check.name}' does not accept extra arguments; "
                "run an atomic member check instead "
                f"(members: {', '.join(check.members or ())})"
            )
        return _run_alias(cfg, check, quiet=args.quiet)

    outcome = run_one(cfg, check, extra=extra, quiet=args.quiet)
    if isinstance(outcome, int):
        return outcome
    return outcome.exit_code


def cmd_show(args: argparse.Namespace) -> int:
    cfg = _load(args)
    run_dir = resolve_run_dir(cfg.runs_dir, args.ref)
    if run_dir is None:
        return _fail("no matching run found (nothing has been run yet?)")
    digest_path = run_dir / DIGEST_NAME
    if not digest_path.exists():
        return _fail(f"run {run_dir.name} has no {DIGEST_NAME}")
    try:
        doc = json.loads(digest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _fail(f"run {run_dir.name} has corrupt {DIGEST_NAME}")
    if not isinstance(doc, dict):
        return _fail(f"run {run_dir.name} digest root is not an object")
    print(dump_json_pretty(doc), end="")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    cfg = _load(args)
    dirs = list_run_dirs(cfg.runs_dir)[-args.n :]
    for run_dir in dirs:
        status = check = "?"
        digest_path = run_dir / DIGEST_NAME
        if digest_path.exists():
            try:
                doc = json.loads(digest_path.read_text(encoding="utf-8"))
                status = str(doc.get("status", "?"))
                check = str(doc.get("check", "?"))
            except json.JSONDecodeError:
                status = "corrupt"
        print(f"{run_dir.name}\t{check}\t{status}")
    return 0


def cmd_checks(args: argparse.Namespace) -> int:
    cfg = _load(args)
    for name in sorted(cfg.checks):
        check = cfg.checks[name]
        if check.is_alias:
            members = ",".join(check.members or ())
            print(f"{name}\talias={members}\tfail_fast={check.fail_fast}")
        else:
            print(f"{name}\tparser={check.parser}\t{check.command}")
    return 0


def cmd_gc(args: argparse.Namespace) -> int:
    cfg = _load(args)
    removed = prune(cfg.runs_dir, args.keep if args.keep is not None else cfg.run.keep)
    print(f"removed {removed} run directorie(s)")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    target = Path.cwd() / CONFIG_NAME
    if target.exists():
        return _fail(f"{target} already exists; refusing to overwrite")
    target.write_text(STARTER_CONFIG, encoding="utf-8")
    print(f"wrote {target}")
    print("reminder: add `.agent-runs/` to .gitignore")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ckdn",
        description="Deterministic check runner and log digester.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_config(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", help=f"path to {CONFIG_NAME}")

    p_run = sub.add_parser("run", help="run a configured check and emit its digest")
    add_config(p_run)
    p_run.add_argument("check", help="check name from ckdn.toml")
    p_run.add_argument("--quiet", action="store_true", help="do not print the digest")
    p_run.add_argument(
        "extra",
        nargs="*",
        default=[],
        help="extra arguments appended to the command, after a `--` separator",
    )
    p_run.set_defaults(fn=cmd_run)

    p_show = sub.add_parser("show", help="print a stored digest (latest by default)")
    add_config(p_show)
    p_show.add_argument("ref", nargs="?", help="run directory name")
    p_show.set_defaults(fn=cmd_show)

    p_list = sub.add_parser("list", help="list recent runs")
    add_config(p_list)
    p_list.add_argument("-n", type=int, default=10)
    p_list.set_defaults(fn=cmd_list)

    p_checks = sub.add_parser("checks", help="list configured checks")
    add_config(p_checks)
    p_checks.set_defaults(fn=cmd_checks)

    p_gc = sub.add_parser("gc", help="prune old run directories")
    add_config(p_gc)
    p_gc.add_argument("--keep", type=int, default=None)
    p_gc.set_defaults(fn=cmd_gc)

    p_init = sub.add_parser("init", help="write a starter ckdn.toml")
    p_init.set_defaults(fn=cmd_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # Split extra command arguments off manually: argparse cannot accept
    # dash-prefixed values in a nargs="*" positional even after `--`.
    extra: list[str] = []
    if "--" in raw:
        idx = raw.index("--")
        raw, extra = raw[:idx], raw[idx + 1 :]
    args = build_arg_parser().parse_args(raw)
    if extra:
        args.extra = [*getattr(args, "extra", []), *extra]
    handler = cast("Callable[[argparse.Namespace], int]", args.fn)
    try:
        return handler(args)
    except ConfigError as exc:
        return _fail(str(exc))
    except BrokenPipeError:
        # stdout piped into head/less and closed early; not an error.
        with contextlib.suppress(OSError):
            sys.stdout.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
