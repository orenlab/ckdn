<!--
SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
SPDX-License-Identifier: MIT
-->
# Contributing

Thanks for helping with **ckdn** (checkdown).

## Development setup

```bash
uv sync --extra dev --extra mcp
uv run pre-commit install   # installs the pre-commit and pre-push hooks
uv run pytest -q --cov=src --cov-report=term-missing
uv run ruff check src tests
uv run mypy src/ckdn
uv run ty check src/ckdn
```

ckdn checks itself: `uv run ckdn run --all` drives the same tools through the
config in `ckdn.toml` and prints one aggregate digest.

Core package stays **stdlib only** (`dependencies = []`). The MCP server is the optional
`mcp` extra (`fastmcp`); sync it for MCP tests and local `ckdn-mcp`.

## Coverage

Coverage of `src` is **100%** and the gate (`fail_under` in `pyproject.toml`)
is enforced in CI on every supported Python and by the `pre-push` hook — so a
new branch without a test fails before review, not after.

Two deliberate exceptions, both visible in the diff rather than hidden in a
percentage:

- `ckdn/_win32.py` is omitted from the measurement. It binds Win32 APIs
  through ctypes and cannot be imported off Windows; the Windows CI job
  exercises it for real.
- A line that is genuinely unreachable (a platform branch, a guard the types
  already rule out) gets `# pragma: no cover` **with the reason next to it**.
  If you cannot write that reason, the line wants a test — or deleting.

One consequence worth knowing before you hit it: the gate runs on **every**
Python in the matrix, so a `sys.version_info` branch is uncovered on the
versions that do not take it and fails the build there. There is no such
branch in `src` today. If you need one, it wants a `# pragma: no cover` with
that as the reason — or a redesign that keeps the code version-agnostic.

## Pull requests

1. Keep changes focused; prefer small PRs.
2. Add or update tests for behavior changes (especially reconcile /
   parser guards and digest shape). Every new line needs one — see
   [Coverage](#coverage).
3. Update [CHANGELOG.md](CHANGELOG.md) under `[Unreleased]` for user-facing changes.
4. Do not commit `.agent-runs/`, `.venv/`, or CodeClone state
   (`.codeclone/`, baseline updates unless explicitly requested).
5. Fill in the PR template.

## Coding norms

- Python ≥ 3.11, **stdlib only** in the published package (`dependencies = []`).
- Parsers report facts; they never decide final status (`ckdn.reconcile` does).
- Digests stay sparse (`ckdn.digest/2`): omit empty / zero / false defaults.
- Prefer machine-readable artifacts under `{run_dir}` over terminal scraping.

## Security

See [SECURITY.md](SECURITY.md). Do not file public issues for vulnerabilities.

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
