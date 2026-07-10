<!--
SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
SPDX-License-Identifier: MIT
-->
# Contributing

Thanks for helping with **ckdn** (checkdown).

## Development setup

```bash
uv sync --extra dev
uv run pre-commit install   # if you use pre-commit
uv run pytest
uv run ruff check src tests
uv run mypy src/ckdn
uv run ty check src/ckdn
# coverage check needs pytest-cov (in the dev extra):
# uv run ckdn run coverage
```

## Pull requests

1. Keep changes focused; prefer small PRs.
2. Add or update tests for behavior changes (especially reconcile /
   parser guards and digest shape).
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
