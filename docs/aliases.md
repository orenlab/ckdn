---
icon: lucide/layers
---

# Aliases & aggregates

An alias groups atomic checks: `ckdn run lint` runs its members in the order of
its own `members` array, not the order the checks happen to appear in
`ckdn.toml`. That array is the schedule — under `fail_fast` it decides which
member is reached and which is skipped, so put the cheap, decisive check
first. Every member gets its **own run directory and digest** — the aggregate
on stdout (`ckdn.aggregate/1`) is a routing document, not a replacement for
member evidence:

```json
{
  "schema": "ckdn.aggregate/1",
  "alias": "lint",
  "status": "fail",
  "rc": 1,
  "members": [
    { "check": "ruff", "status": "pass", "rc": 0 },
    {
      "check": "pylint",
      "status": "fail",
      "rc": 1,
      "run_dir": ".agent-runs/20260707T101500Z-pylint"
    }
  ]
}
```

The aggregate contract:

- **`status`** — `pass` iff every member passed; otherwise `fail`. Only this
  top-level field collapses to pass/fail. Each member row carries its real
  four-value status, so an `error` or a `parse_mismatch` member is visible in
  the aggregate itself — you do not have to open its digest to find out that
  the red was never a verdict.
- **`rc`** — `130` for an interrupted series, whatever the members returned;
  otherwise the first member's nonzero exit code (clamped 1–255), else `1` if
  any member is non-green while its own `rc` was `0` (gate failure /
  mismatch), else `0`. It is the process exit code too — except under
  `--gate`, which exits on the baseline gate instead of on execution.
- **`interrupted`** — present, always `true`, when Ctrl-C cut the series
  short. The members after it were never attempted, and an interrupt ends the
  sequence even with `fail_fast = false`: once you have stopped the run, the
  remaining checks are work you asked not to do. Without this key a truncated
  series would be indistinguishable from one that ran to completion and
  failed.
- **`fail_fast = true`** (default) stops after the first non-green member;
  members after it are **not run** and do not appear in the aggregate —
  `members` lists only the checks that actually ran. With `fail_fast = false`
  every member runs and appears with a real status.
- A member's `run_dir` is the same relative, posix path its own digest reports
  (passing members carry no `run_dir` in the aggregate).
- Extra args after `--` are rejected on aliases — pass them to the atomic
  check (`ckdn run ruff -- -x`).
- `--fail-fast` overrides a configured `fail_fast` for this run. It is
  presence-only — there is no `--no-fail-fast`, so the flag can tighten an
  alias but never loosen one — and it is rejected on an atomic check, which
  has no sequence to stop.

Read the aggregate to decide *which* member digest to open
(`ckdn show <run-dir>`), then work from that digest.

## Run everything: `ckdn run --all`

`ckdn run --all` runs **every atomic check** in the order they appear in
`ckdn.toml` — here file order really is the schedule, since there is no
`members` array to set one (aliases are skipped; they only group atomics).
It emits one `ckdn.aggregate/1` with `alias = "*"`, and runs every check by
default; `--fail-fast` stops at the first non-green one. Same exit-code and
routing rules as an alias aggregate, so it drops into CI as a single "verify
the project" step.

```bash
ckdn run --all              # every atomic check, report them all
ckdn run --all --fail-fast  # stop at the first failure
```
