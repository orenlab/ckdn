<!--
SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
SPDX-License-Identifier: MIT
-->
---
name: verified-fix-loop
description: Run project checks (tests, coverage, types, lint, builds) through ckdn and fix findings in a bounded loop. Use this skill whenever the task is to fix failing tests, restore coverage, resolve type or lint errors, make CI green, or verify that edits did not break anything — even if the user does not mention ckdn by name. Never run pytest/ruff/ty directly and never read raw tool logs when a ckdn check exists for them.
---

# Verified Fix Loop

Run every verification through `ckdn run <check>` and read the digest it
prints (or `.agent-runs/latest/digest.json`). Do not read `full.log` unless
the digest's `status` is `error` or `parse_mismatch` — and even then, start
from `log_tail` inside the digest and use targeted `grep` on `full.log`,
never `cat` it whole.

Discover available checks with `ckdn checks`. Aliases (e.g. `lint`,
`types`) expand to their atomic members; prefer the alias for a group
run, or an atomic name (`ruff`, `ty`) when fixing one tool.

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
stdout is the aggregate only; open a member `run_dir` via `ckdn show`
when fixing a red member.

Never declare success based on log text, partial output, or your own
reading of the tool's message. If `ckdn run` did not end with
`status: "pass"`, the check did not pass.

## Loop shape

Before the first attempt, fix these parameters (ask the user if absent):

- which checks must pass (e.g. `tests`, `coverage`, `types`, `lint`)
- the edit scope (which files you may touch)
- maximum attempts — default 5 if the user did not set one

Each attempt:

1. `ckdn run <check>` — read the digest from stdout.
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

Report per check: status before → after, the exact `ckdn run` command,
files changed, findings intentionally left unfixed (with reasons), and
attempts used. Quote statuses from digests, not from memory.
