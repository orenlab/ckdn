---
icon: lucide/compass
---

# Design & non-goals

## Design principles

- **Exit-code-first** — parsing only makes the verdict stricter.
- **Agree-or-alarm** — `pass` requires exit code and parser to agree.
- **Reports over regexes** — JUnit / coverage XML / JSON where possible.
- **Facts ≠ policy** — digests vs skills / project rules.
- **Determinism where it pays** — digest vs meta split.
- **No shell** — exit codes are not laundered through pipelines.
- **Stdlib only** — the guard of dependency behavior brings none of its own.

## Non-goals (for now)

- Parallel member execution and global `ckdn run --all` (named aliases cover
  lint/types groups; full-suite sequencing stays with the caller)
- Watch mode, TUI, HTML dashboards
- Windows symlink handling beyond the `LATEST` marker fallback

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests
uv run mypy src
```

Entry point: `ckdn` → `ckdn.cli:main`.

Contract tests pin the status-model invariants; parser tests pin fact
extraction and loud-failure guards; schema tests validate every emitted
document against its published JSON Schema.

## Building these docs

```bash
uv run --with zensical zensical serve   # live preview
uv run --with zensical zensical build   # render to site/
```

## License & community

- Copyright (c) 2026 Den Rozhnovskiy &lt;rozhnovskiydenis@gmail.com&gt;
- License: [MIT](https://github.com/orenlab/ckdn/blob/main/LICENSE)
  (`SPDX-License-Identifier: MIT`)
- [Changelog](https://github.com/orenlab/ckdn/blob/main/CHANGELOG.md) ·
  [Security](https://github.com/orenlab/ckdn/blob/main/SECURITY.md) ·
  [Contributing](https://github.com/orenlab/ckdn/blob/main/CONTRIBUTING.md) ·
  [Code of Conduct](https://github.com/orenlab/ckdn/blob/main/CODE_OF_CONDUCT.md)
