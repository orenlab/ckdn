<!--
SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
SPDX-License-Identifier: MIT
-->
---
name: verified-fix-loop
description: Run project checks (tests, coverage, types, lint, pre-commit hooks, builds) through ckdn and fix findings
in a bounded loop. Use this skill whenever the task is to fix failing tests, restore coverage, resolve type or lint
errors, make CI green, or verify that edits did not break anything — even if the user does not mention ckdn by name.
Never run pytest/ruff/ty/pre-commit directly and never read raw tool logs when a ckdn check exists for them. When ckdn
MCP is connected, prefer MCP tools over shell; pass cwd on every call when the project root differs from the config file
location (worktrees, Glass slices).
---

# Verified Fix Loop

Run every verification through ckdn and read the digest (`ckdn.digest/2`).
Do not read `full.log` unless the digest's `status` is `error` or
`parse_mismatch` — and even then, start from `log_tail` inside the digest
and use targeted `grep` on `full.log`, never `cat` it whole.

Discover available checks with `ckdn checks` (CLI) or `list_checks` (MCP).
Aliases (e.g. `lint`, `types`, `style`, `hooks`) expand to atomic members;
prefer the alias for a group run, or an atomic name (`ruff`, `ty`,
`pre_commit`) when fixing one tool.

## Shell vs MCP

Use **one** integration path per session — do not mix shell `ckdn run` and
MCP `run_check` for the same fix loop unless the user asks to switch.

| Goal               | CLI                    | MCP            |
|--------------------|------------------------|----------------|
| Discover checks    | `ckdn checks`          | `list_checks`  |
| Run atomic check   | `ckdn run <check>`     | `run_check`    |
| Run alias          | `ckdn run <alias>`     | `run_group`    |
| Read latest digest | stdout / `ckdn show`   | `get_digest`   |
| Bounded evidence   | `ckdn show --evidence` | `get_evidence` |

MCP trust rules match the CLI: `fail` / `error` / `parse_mismatch` are
normal structured results, not tool failures. Only configured checks —
never invent shell commands.

## Working directory (`cwd`)

Subprocesses and relative `.agent-runs/` resolve from **cwd**, not from
where `ckdn.toml` lives.

Resolution order (CLI and MCP): per-call `--cwd` / `cwd` argument →
`CKDN_CWD` → `ckdn-mcp --cwd` (MCP server default) → process cwd.

**Worktree / Glass / temp-config slices:** when `ckdn.toml` is outside
the project tree (e.g. config in `/tmp`, code in a worktree), pass the
**project root** as cwd on every invocation:

```bash
ckdn run --config /tmp/ckdn.toml --cwd /path/to/worktree tests
```

```json
{
  "check": "tests",
  "config": "/tmp/ckdn.toml",
  "cwd": "/path/to/worktree"
}
```

Omitting cwd in that layout runs tools in the wrong directory and writes
evidence to the wrong `.agent-runs/`.

`lock-config` and `verify-config` are **CLI/CI only** — not MCP tools.

## The only source of truth

The digest's `status` field is the verdict. There are four values:

- `pass` — the check is green. This is the only green state.
- `fail` — findings or a gate failure; fix the findings.
- `error` — the tool itself broke (collection error, missing binary,
  timeout). Fix the environment or invocation, not the code under test.
- `parse_mismatch` — the exit code and the parsed output disagree.
  Treat as red. Report it to the user; do not work around it.

Digests use schema `ckdn.digest/2` (sparse): missing keys mean empty /
`0` / `false`. Do not require `findings: []` on a green run. On aliases,
stdout is the aggregate only; open a member `run_dir` via `ckdn show` or
`get_evidence` when fixing a red member.

Never declare success based on log text, partial output, or your own
reading of the tool's message. If the check did not end with
`status: "pass"`, it did not pass.

## Pre-commit and hooks

When `ckdn.toml` defines `pre_commit` (parser `pre_commit`) or alias
`hooks`, run those through ckdn — not `pre-commit run` directly. The
digest surfaces per-hook findings on failure and hook counts in `summary`.
For full-repo hook parity, the configured command should include
`--all-files` (see project `ckdn.toml`).

## Loop shape

Before the first attempt, fix these parameters (ask the user if absent):

- which checks must pass (e.g. `tests`, `coverage`, `types`, `lint`,
  `hooks`)
- the edit scope (which files you may touch)
- maximum attempts — default 5 if the user did not set one
- cwd when config and project root differ

Each attempt:

1. Run the check (CLI or MCP) — read the digest from the result.
2. Pick the first root cause from `findings`, not every symptom at once.
3. Read the relevant source and tests before editing.
4. Make the smallest edit that addresses the root cause.
5. Re-run the same check. Only move to the next check when this one passes.

Stop and report BLOCKED when: attempts are exhausted, a fix requires files
outside the declared scope, or a deterministic blocker appears (`error` /
`parse_mismatch` you cannot resolve by fixing your own invocation).

## Forbidden moves

Never fix a red check by:

- lowering thresholds (`fail_under`, lint rule sets, type strictness)
- deleting or weakening tests or assertions
- adding tests that execute lines without asserting behavior
- editing `ckdn.toml` to change what a check verifies
- editing anything under `.agent-runs/` (it is evidence, not workspace)
- silently expanding the edit scope

Any of these requires explicit user approval, requested in plain terms.

## Final report contract

Report per check: status before → after, the exact command or MCP tool
call (including `config` and `cwd` when used), files changed, findings
intentionally left unfixed (with reasons), and attempts used. Quote
statuses from digests, not from memory.
