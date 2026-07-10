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

- Optional FastMCP stdio server (`ckdn[mcp]` / `ckdn-mcp`) as a thin adapter
  over a shared stdlib `ckdn.app` layer: `list_checks`, `run_check`,
  `run_group`, `get_digest`, `list_runs`, `get_evidence`
- Application facade (`ckdn.app`) shared by CLI and MCP so reconcile/digest
  semantics stay single-sourced

## [1.0.0] - 2026-07-10

### Added

- Initial public release of **ckdn** (checkdown): deterministic check runner
  and bounded log digester for AI-assisted development loops
- Atomic checks and configurable aliases (`members`, optional `fail_fast`)
- Tier-1 parsers: pytest, ruff, coverage, ty, mypy, pyright, pylint, bandit,
  pip-audit, SARIF, reformat text, generic
- Sparse digest schema `ckdn.digest/2` and meta schema `ckdn.meta/1`
- CLI: `ckdn run`, `ckdn show`, starter `ckdn.toml`
- Stdlib-only runtime; MIT license; security and contribution docs

[Unreleased]: https://github.com/orenlab/ckdn/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/orenlab/ckdn/releases/tag/v1.0.0
