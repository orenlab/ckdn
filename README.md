<!--
SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
SPDX-License-Identifier: MIT
-->
<div align="center">

  <picture>
    <source
      media="(prefers-color-scheme: dark)"
      srcset="https://raw.githubusercontent.com/orenlab/ckdn/refs/heads/main/assets/ckdn-wordmark-dark.svg"
    >
    <source
      media="(prefers-color-scheme: light)"
      srcset="https://raw.githubusercontent.com/orenlab/ckdn/refs/heads/main/assets/ckdn-wordmark.svg"
    >
    <img
      alt="ckdn — deterministic check runner and log digester for AI-assisted development loops"
      src="https://raw.githubusercontent.com/orenlab/ckdn/refs/heads/main/assets/ckdn-wordmark.svg"
      width="220"
    >
  </picture>

  <p><strong>Deterministic check runner and log digester for AI-assisted development loops</strong></p>

  <p>
    <em>
      Let agents move fast.<br>
      Keep verification explicit, bounded, and machine-readable.
    </em>
  </p>

  <p>
    <a href="https://pypi.org/project/ckdn/"><img src="https://img.shields.io/pypi/v/ckdn.svg?color=3dd68c&logo=pypi&logoColor=white" alt="PyPI"/></a>
    <a href="https://github.com/orenlab/ckdn/actions/workflows/ci.yml"><img src="https://github.com/orenlab/ckdn/actions/workflows/ci.yml/badge.svg" alt="CI"/></a>
    <a href="https://orenlab.github.io/ckdn/"><img src="https://img.shields.io/badge/docs-orenlab.github.io%2Fckdn-3dd68c?logo=readthedocs&logoColor=white" alt="Documentation"/></a>
    <a href="https://github.com/orenlab/ckdn/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-3dd68c?logo=opensourceinitiative&logoColor=white" alt="MIT license"/></a>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-%3E%3D3.11-3dd68c?logo=python&logoColor=white" alt="Python ≥ 3.11"/></a>
    <a href="https://github.com/orenlab/ckdn"><img src="https://img.shields.io/badge/core-stdlib%20only-3dd68c?logo=python&logoColor=white" alt="core: stdlib only"/></a>
    <a href="https://orenlab.github.io/ckdn/digests/"><img src="https://img.shields.io/badge/digest-ckdn.digest%2F2-3dd68c?logo=json&logoColor=white" alt="digest schema v2"/></a>
  </p>

</div>

---

**ckdn** (short for **checkdown**) sits between a coding agent and your
project’s verification tools. The agent never reads a 10 000-line pytest
log and never decides from prose whether a run “looks green”.

Every check goes through one orchestrator that:

1. owns the true process exit code,
2. archives the full log as evidence,
3. emits a **bounded, machine-readable digest** — the only thing the agent
   is supposed to read.

<div align="center">
  <picture>
    <source
      media="(prefers-color-scheme: dark)"
      srcset="https://raw.githubusercontent.com/orenlab/ckdn/refs/heads/main/assets/ckdn-pipeline-dark.svg"
    >
    <img
      alt="ckdn pipeline: agent → ckdn run coverage → subprocess (owns exit code) → .agent-runs artifacts; agent reads digest.json"
      src="https://raw.githubusercontent.com/orenlab/ckdn/refs/heads/main/assets/ckdn-pipeline.svg"
      width="680"
    >
  </picture>
</div>

Text-based interpretation of tool output fails two ways: **context bloat**
(the agent drowns in a thousand-line log) and **false green** (a collection
error prints no `FAILED` lines, so a regex calls it clean). ckdn's answer is a
strict status model from *both* the exit code and a format-aware parser: the
two must agree before anything is called green. ckdn may **downgrade** green;
it never **upgrades** red.

Runtime: Python ≥ 3.11, **stdlib only** for the core CLI (zero third-party
dependencies). The optional MCP server is an extra (`ckdn[mcp]`).

## Install

```bash
uv tool install ckdn          # global CLI
# or as a project dev dependency:
uv add --dev ckdn
```

## Quick start

```bash
cd your-project
ckdn init                      # writes starter ckdn.toml
echo '.agent-runs/' >> .gitignore

ckdn checks                    # list configured checks
ckdn run ruff                  # one atomic check → compact digest on stdout
ckdn run lint                  # alias → members (e.g. ruff, pylint)
ckdn show                      # pretty-print latest digest
```

`ckdn run` exits with the original command’s code, so it slots into any hook or
CI step where the raw command used to be — with a bounded, schema-backed
digest as a side effect.

## Documentation

Full documentation lives at **[orenlab.github.io/ckdn](https://orenlab.github.io/ckdn/)**:

- [Get started](https://orenlab.github.io/ckdn/get-started/) — install and first check
- [Status model](https://orenlab.github.io/ckdn/status-model/) — exit code × parser → one verdict; exit-code contract
- [Digests & schemas](https://orenlab.github.io/ckdn/digests/) — the machine-readable contract (`ckdn.digest/2`)
- [Aliases & aggregates](https://orenlab.github.io/ckdn/aliases/) — grouped checks and routing
- [Configuration](https://orenlab.github.io/ckdn/configuration/) — `ckdn.toml`, command policy, working directory
- [CLI](https://orenlab.github.io/ckdn/cli/) — command reference
- [Parsers](https://orenlab.github.io/ckdn/parsers/) — built-ins, custom parsers, and plugins
- [Agents & MCP](https://orenlab.github.io/ckdn/agents-mcp/) — wiring ckdn into an agent loop
- [Design & non-goals](https://orenlab.github.io/ckdn/design/)

## License & community

- Copyright (c) 2026 Den Rozhnovskiy \<rozhnovskiydenis@gmail.com\>
- License: [MIT](LICENSE) (`SPDX-License-Identifier: MIT`)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Security: [SECURITY.md](SECURITY.md)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Code of Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
