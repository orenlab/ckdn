---
icon: lucide/layers
---

# Aliases & aggregates

An alias groups atomic checks: `ckdn run lint` runs each member in config
order. Every member gets its **own run directory and digest** — the aggregate
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

- **`status`** — `pass` iff every member passed; otherwise `fail`. The
  aggregate collapses to pass/fail; a member's own `error` / `parse_mismatch`
  shows only in that member's digest.
- **`rc`** (also the process exit code) — the first member's nonzero exit code
  (clamped 1–255), else `1` if any member is non-green while its own `rc` was
  `0` (gate failure / mismatch), else `0`.
- **`fail_fast = true`** (default) stops after the first non-green member;
  members after it are **not run** and do not appear in the aggregate —
  `members` lists only the checks that actually ran. With `fail_fast = false`
  every member runs and appears with a real status.
- A member's `run_dir` is the same relative, posix path its own digest reports
  (passing members carry no `run_dir` in the aggregate).
- Extra args after `--` are rejected on aliases — pass them to the atomic
  check (`ckdn run ruff -- -x`).

Read the aggregate to decide *which* member digest to open
(`ckdn show <run-dir>`), then work from that digest.

## Run everything: `ckdn run --all`

`ckdn run --all` runs **every atomic check** in config order (aliases are
skipped — they only group atomics) and emits one `ckdn.aggregate/1` with
`alias = "*"`. It runs all checks by default; `--fail-fast` stops at the first
non-green one. Same exit-code and routing rules as an alias aggregate, so it
drops into CI as a single "verify the project" step.

```bash
ckdn run --all              # every atomic check, report them all
ckdn run --all --fail-fast  # stop at the first failure
```
