<!--
SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
SPDX-License-Identifier: MIT
-->
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/orenlab/ckdn/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/orenlab/ckdn/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/orenlab/ckdn/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/orenlab/ckdn/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/orenlab/ckdn/releases/tag/v1.0.0
