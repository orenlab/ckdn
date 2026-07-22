<!--
SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
SPDX-License-Identifier: MIT
-->
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] - 2026-07-22

### Added

- `ckdn doctor`: static pre-flight diagnostics over `ckdn.toml` before any
  subprocess — flags executables missing from `PATH` (error) and commands that
  do not match their parser (warning: a file-based parser whose command never
  writes its report, or a missing `--output json` / `--outputjson` / `--check`
  flag). Exit `1` on errors (or on warnings with `--strict`), so it drops into
  CI as a config gate
- `--json` on `ckdn list` and `ckdn checks`: machine-readable
  `{"runs": [...]}` / `{"checks": [...]}` (the same shape the MCP `list_runs`
  / `list_checks` tools return)
- Per-check `env` table: overlays the subprocess environment for one check
  (inherited `PATH` etc. preserved), with `{run_dir}` substitution in values;
  never recorded in `meta.json`
- `ckdn run --all [--fail-fast]`: run every atomic check in config order and
  emit one `ckdn.aggregate/1` (`alias = "*"`); a single "verify the project"
  step for CI
- `ckdn annotate [ref] [--format github|sarif]`: project a stored digest's
  findings to GitHub Actions annotations (inline on the PR) or a SARIF 2.1.0
  document (code scanning) — a pure projection that never changes run status
- **Finding baselines** — gate CI on *new* findings without fixing the whole
  backlog. `[run].baseline` + `ckdn baseline <check>` record accepted findings
  (line/column-drift-tolerant fingerprints); the digest gains `baseline`
  (`known`/`new`) and a `gate` (`pass`/`fail`/`unavailable`) reported
  **separately** from execution status. `ckdn run --gate` makes the exit
  reflect the gate for CI. Baseline never changes execution truth and never
  accepts an untrusted failure (`error`/`parse_mismatch`/crash → `unavailable`)

### Changed

- Full Windows: the test suite now runs end-to-end on Windows. The command
  and artifact path-escape checks already fire cross-platform (pathlib anchors
  a rooted `/etc/...` path to the drive root, outside the workspace), so those
  tests no longer skip; the app/MCP tests isolate `execute` on every OS (real
  subprocess execution stays covered by `test_runner` via `sys.executable`).
  Only the real-symlink test remains POSIX-only (Windows symlinks need
  privilege; the `LATEST` marker fallback is covered separately)

### Fixed

- **Critical: a hung check could hang the whole machine.** `execute()` read the
  child's output through a pipe, whose write end every descendant inherits, so
  draining it blocked until *all* of them exited. Killing the direct child
  (`uv`) left `pytest` and its workers holding the pipe, and ckdn waited on EOF
  forever while the orphans kept burning CPU. Interrupting produced an empty
  run directory — no log, no meta, no digest — because artifacts were only
  written after the subprocess returned.
  - The log now streams straight into `full.log`: no pipe, no deadlock, and
    partial evidence survives an interrupt.
  - The child starts in its own process group (POSIX session /
    Windows `CREATE_NEW_PROCESS_GROUP`) and the whole **group** is terminated
    on timeout, on `SIGINT`, on a clean exit, and on any other path:
    `SIGTERM` → grace → `SIGKILL` for whatever is still there. Escalation
    watches the group rather than the direct child — a wrapper like `uv` dies
    on the first `SIGTERM` while the tool it launched ignores it, and waiting
    on the child alone meant `SIGKILL` was never sent. Two limits are stated
    in the [status model](docs/status-model.md) rather than promised away: a
    check that detaches into its own session escapes the group, and `kill -9`
    on ckdn itself runs no cleanup at all.
  - Ctrl-C is delivered while a check runs. `Popen.wait(None)` blocks
    uninterruptibly on Windows, so keypresses were ignored entirely there;
    the wait now polls. A second Ctrl-C during the grace period, or one during
    parsing or the evidence write, no longer abandons the run directory
    without a digest.
  - `latest` is published by rename. Unlinking it first left a window with no
    pointer at all, and two runs finishing together could both unlink and race
    to create — the loser falling back to the `LATEST` marker, leaving two
    pointers that disagreed about which run was newest.
  - Lock file names are unique per check. The sanitizer mapped every unsafe
    character to `_`, so `py.test` and `py_test` shared one lock: each refused
    to start while the *other* ran, and reported the other as a run that did
    not exit cleanly.
  - Run locks are real kernel file locks (`flock` / `msvcrt.locking`) instead
    of a pid file. The old protocol could hand one check to two runs through
    three separate races, could not see a second *thread* of ckdn's own
    process (which is how the MCP server runs checks), wedged a check
    permanently on a pid too wide for a C `int`, and turned a failed unlink
    into a permanent false "did not exit cleanly" note.
  - New `rc=130` plus an `interrupted: true` field on the digest, the
    aggregate and `meta.json` — a reason, like `timed_out`. Code that only
    reads `status` still just sees `error`, but the **schema documents are
    closed** (`additionalProperties: false`): a validator pinned to the copies
    exported from 1.2.0 will reject the new field, so re-export them with
    `ckdn schema` when upgrading. Being cut short outranks every other signal
    in reconcile — by Ctrl-C *or* by timeout — so partial evidence can never
    be read as `fail` or `parse_mismatch`.
  - Alias and `--all` sequences stop on interrupt instead of starting the next
    check, and the aggregate exits `130` rather than passing through an
    earlier red member's code; the CLI exits `130` instead of a traceback.
  - `meta.json`'s `log_sha256`/`log_bytes` describe `full.log` as it sits on
    disk. They were computed from the decoded text, which collapses CRLF, so
    an independent `sha256 full.log` disagreed for almost any Windows tool's
    output and read as tampered evidence.
  - `ckdn baseline` refuses to record an interrupted or untrusted run instead
    of overwriting the accepted findings with a partial set — which would
    announce the whole existing backlog as new on the next gate.
  - Pruning skips run directories that have no digest yet, so retiring old
    runs of one check can no longer delete another check's run mid-write.
  - `AppError` (a refused start, e.g. a lock conflict) is reported as
    `ckdn: …` with exit `2` on every command. `run --all` and `baseline` let
    it escape as a traceback with exit `1` — the code that means "this check
    is red", which CI could not tell apart from a real failure.
  - Runs are serialized per `(runs_dir, check)`: a second concurrent run of the
    same check is refused (stale locks are reclaimed), so a hung run cannot be
    compounded by a retry.
  - Reclaiming a stale lock says so in the run's notes: the previous run did
    not exit cleanly and may have left processes behind. A run killed with
    `SIGKILL` executes no cleanup, so this is the only honest signal left. It
    is advisory — it describes the *previous* run and never changes this run's
    status — and ckdn stops nothing on its own: only its own pid is ever
    recorded (never the child's process group), and a recycled pid would make
    an automatic kill land on an unrelated process.

## [1.2.0] - 2026-07-21

### Added

- JSON Schema documents (Draft 2020-12) for `ckdn.digest/2`,
  `ckdn.aggregate/1`, and `ckdn.meta/1`, shipped in the wheel under
  `ckdn/schemas/`; loadable via `ckdn.schema.load_schema()` (stdlib-only). The
  test suite validates every emitted document shape against these schemas, so
  a structural drift fails CI
- `ckdn schema [id]` command: print a packaged JSON Schema or list schema ids,
  so downstream can wire ckdn's contract into their own validation
- Parser plugin discovery via the `ckdn.parsers` entry-point group: built-in
  names take precedence and are never shadowed, and a broken/colliding plugin
  is skipped rather than raised; fork-and-own registration still supported
- Documentation site (Zensical) under `docs/` with a GitHub Pages publish
  workflow; determinism and cross-OS byte-stability now guarded by tests
- Windows added to the CI test matrix; POSIX-specific tests (real
  `true`/`false`, `/etc` paths, symlink privilege) skip on Windows with an
  explicit reason

### Changed

- Digest and aggregate `run_dir` are normalized to forward slashes
  (`Path.as_posix()`) so a digest is byte-identical across operating systems
- Alias aggregate members report the same relative `run_dir` as the member's
  own digest (previously an absolute path)
- Exit-code contract documents the synthetic codes `124` (timeout), `126`
  (blocked by command policy), and `127` (command not found)
- README trimmed to a concise overview; full reference moved to the docs site

## [1.1.1] - 2026-07-13

### Added

- Shared MCP agent guidance (`ckdn.mcp.guidance`): server instructions and
  `cwd` hints on every config-using tool description
- `examples/claude/CLAUDE.md` standing-rule template for ckdn + worktree cwd
- MCP worktree contract test (`cwd` per call when config lives elsewhere)

### Changed

- `verified-fix-loop` skill: MCP tool mapping, `cwd`/worktree rules,
  `pre_commit` / `hooks`, CLI vs MCP division
- README agent/MCP sections: `CKDN_CWD` in client configs, worktree examples,
  shared `cwd` parameter on all tools

## [1.1.0] - 2026-07-13

### Added

- `pre_commit` parser for `pre-commit run` terminal output: per-hook
  findings on failure, hook counts in summary, loud-failure guard when the
  exit code and parsed failures disagree
- Starter `ckdn.toml` enables `format`, `pre_commit`, and `lock`
  (`uv lock --check`) checks plus `style` and `hooks` aliases
- CLI `--cwd` flag and `CKDN_CWD` env var: subprocess working directory and
  relative `runs_dir` resolve from invocation cwd, not the config file parent
- MCP tools accept optional `cwd`; `ckdn-mcp` supports `--cwd`
- Command policy (default ``workspace``): argv path tokens must resolve inside
  ``cwd``; sensitive system locations are rejected; subprocess is not started on
  violation (``rc=126``)
- ``command_policy = "allowlist"`` with optional ``[run.command_allowlist]``
  prefixes; ``off`` disables checks for exotic workflows
- ``ckdn lock-config`` / ``ckdn verify-config`` (+ ``--locked``) for command
  digest governance in CI

### Fixed

- Parser artifact paths: after `{run_dir}` substitution, absolute paths are
  returned as-is instead of being joined under `run_dir` again (fixes
  `parse_mismatch` when JUnit lives at an absolute path under the run directory)
- Worktree / temp-config slices: a `ckdn.toml` under `/tmp` no longer forces
  subprocess `cwd` to the config directory when the project lives elsewhere
- Parser artifact reads are confined to the run directory after `resolve()`
  (rejects `/etc/passwd`, `..` escapes, and symlink hops) before any file open

## [1.0.0] - 2026-07-11

### Added

- Initial public release of **ckdn** (checkdown): deterministic check runner
  and bounded log digester for AI-assisted development loops
- Atomic checks and configurable aliases (`members`, optional `fail_fast`)
- Tier-1 parsers: pytest, ruff, coverage, ty, mypy, pyright, pylint, bandit,
  pip-audit, SARIF, reformat text, generic
- Sparse schemas: digest `ckdn.digest/2`, alias aggregate `ckdn.aggregate/1`,
  meta `ckdn.meta/1`
- CLI: `run`, `show`, `list`, `checks`, `gc`, `init` (writes a starter
  `ckdn.toml`)
- Stdlib-only core runtime; MIT license; security and contribution docs
- Optional FastMCP stdio server (`ckdn[mcp]` / `ckdn-mcp`) exposing
  `list_checks`, `run_check`, `run_group`, `get_digest`, `list_runs`,
  `get_evidence`
- Application facade (`ckdn.app`) shared by CLI and MCP so reconcile/digest
  semantics stay single-sourced

[Unreleased]: https://github.com/orenlab/ckdn/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/orenlab/ckdn/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/orenlab/ckdn/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/orenlab/ckdn/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/orenlab/ckdn/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/orenlab/ckdn/releases/tag/v1.0.0
