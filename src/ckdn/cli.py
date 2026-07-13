# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""ckdn command-line interface.

Thin transport over :mod:`ckdn.app`. Exit code contract of ``ckdn run``:

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
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

from ckdn import __version__
from ckdn.app import (
    AliasRunResult,
    AppError,
    AtomicRunResult,
    get_digest,
    list_checks,
    list_runs,
    run_check,
)
from ckdn.app import run_one as app_run_one
from ckdn.config import (
    CONFIG_NAME,
    STARTER_CONFIG,
    CheckConfig,
    Config,
    ConfigError,
    load_config,
)
from ckdn.config_lock import LOCK_NAME, verify_config, write_config_lock
from ckdn.digest import dump_json, dump_json_pretty
from ckdn.runner import prune


def _fail(message: str) -> int:
    print(f"ckdn: {message}", file=sys.stderr)
    return 2


def _load(args: argparse.Namespace) -> Config:
    cwd = Path(args.cwd).resolve() if getattr(args, "cwd", None) else None
    return load_config(Path(args.config) if args.config else None, cwd=cwd)


def run_one(
    cfg: Config,
    check: CheckConfig,
    *,
    extra: list[str],
    quiet: bool,
) -> AtomicRunResult | int:
    """CLI-compatible wrapper around :func:`ckdn.app.run_one`.

    Returns ``AtomicRunResult``, or ``2`` after printing on :class:`AppError`
    (legacy contract used by tests).
    """
    try:
        result = app_run_one(cfg, check, extra=extra)
    except AppError as exc:
        return _fail(str(exc))
    if not quiet:
        print(dump_json(result.digest), end="")
    return result


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _load(args)
    try:
        outcome = run_check(cfg, args.check, extra=list(args.extra))
    except AppError as exc:
        return _fail(str(exc))
    if isinstance(outcome, AliasRunResult):
        if not args.quiet:
            print(dump_json(outcome.aggregate), end="")
        return outcome.exit_code
    if not args.quiet:
        print(dump_json(outcome.digest), end="")
    return outcome.exit_code


def cmd_show(args: argparse.Namespace) -> int:
    cfg = _load(args)
    try:
        doc = get_digest(cfg, args.ref)
    except AppError as exc:
        return _fail(str(exc))
    print(dump_json_pretty(doc), end="")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    cfg = _load(args)
    for row in list_runs(cfg, limit=args.n):
        print(f"{row['run_id']}\t{row['check']}\t{row['status']}")
    return 0


def cmd_checks(args: argparse.Namespace) -> int:
    cfg = _load(args)
    for item in list_checks(cfg):
        if item["kind"] == "alias":
            members = ",".join(item["members"])
            print(f"{item['name']}\talias={members}\tfail_fast={item['fail_fast']}")
        else:
            print(f"{item['name']}\tparser={item['parser']}\t{item['command']}")
    return 0


def cmd_gc(args: argparse.Namespace) -> int:
    cfg = _load(args)
    removed = prune(cfg.runs_dir, args.keep if args.keep is not None else cfg.run.keep)
    print(f"removed {removed} run directorie(s)")
    return 0


def cmd_lock_config(args: argparse.Namespace) -> int:
    cfg = _load(args)
    target = Path(args.output) if args.output else cfg.config_path.parent / LOCK_NAME
    written = write_config_lock(cfg, target)
    print(f"wrote {written}")
    return 0


def cmd_verify_config(args: argparse.Namespace) -> int:
    cfg = _load(args)
    lock_path = Path(args.lock_file) if args.lock_file else None
    errors = verify_config(cfg, locked=args.locked, lock_path=lock_path)
    if errors:
        for line in errors:
            print(f"ckdn: {line}", file=sys.stderr)
        return 1
    print("ok")
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
        p.add_argument(
            "--cwd",
            help="working directory for subprocesses and relative runs_dir "
            "(default: invocation directory)",
        )

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

    p_lock = sub.add_parser(
        "lock-config",
        help="write ckdn.lock.toml command digests for CI governance",
    )
    add_config(p_lock)
    p_lock.add_argument(
        "-o",
        "--output",
        help=f"lock file path (default: next to config as {LOCK_NAME})",
    )
    p_lock.set_defaults(fn=cmd_lock_config)

    p_verify = sub.add_parser(
        "verify-config",
        help="validate command policy (and optional lock file) without running checks",
    )
    add_config(p_verify)
    p_verify.add_argument(
        "--locked",
        action="store_true",
        help=f"also require commands to match {LOCK_NAME}",
    )
    p_verify.add_argument(
        "--lock-file",
        help=f"path to lock file (default: {LOCK_NAME} beside config)",
    )
    p_verify.set_defaults(fn=cmd_verify_config)

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
