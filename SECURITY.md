<!--
SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
SPDX-License-Identifier: MIT
-->
# Security Policy

## Supported versions

| Version  | Supported |
|----------|-----------|
| 1.x      | Yes       |
| &lt; 1.0 | No        |

## Reporting a vulnerability

**Do not open a public GitHub issue for security reports.**

Please report vulnerabilities privately via GitHub Security Advisories
(preferred) or email:

1. Open https://github.com/orenlab/ckdn/security/advisories/new
2. Or email **rozhnovskiydenis@gmail.com** with subject `[ckdn security]`
3. Include: affected versions, reproduction steps, impact, and any
   suggested fix.

We aim to acknowledge reports within **7 days** and to publish a fix or
mitigation timeline once the issue is confirmed.

## Scope

In scope:

- Remote or local code execution via crafted tool output / artifacts
- Path traversal via `{run_dir}` / config paths that escapes the intended
  workspace
- Secrets leaking into digests, logs, or published artifacts by default
- Privilege escalation when `ckdn` is used as a CI / hook gate

Out of scope (unless you can show a concrete exploit path):

- Misconfiguration of third-party tools that ckdn merely orchestrates
- Social-engineering agents into ignoring digests
- Denial of service by feeding unbounded tool output (digests are bounded;
  `full.log` is intentionally complete evidence)

## Hardening notes for operators

- Treat `.agent-runs/` as sensitive evidence (may contain secrets from
  tool stdout). Keep it out of version control and restrict access in CI
  artifacts.
- Prefer machine-readable reports written into `{run_dir}` over scraping
  terminals.
- Do not run untrusted check commands; `ckdn.toml` is trusted project
  configuration.
