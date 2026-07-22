---
icon: lucide/git-compare
---

# Baselines

A baseline lets you adopt ckdn on a project that already has known findings:
gate CI on **new** problems without fixing the whole backlog first — *don't
break beyond the current state*.

## Baseline never changes execution truth

> Baseline never changes execution truth. A non-zero tool result remains failed
> in the digest. Baseline classifies recognized findings as known or new. CI
> policy may pass when all findings are known, but this gate decision is
> reported separately from execution status. Unknown failures, parser
> mismatches, crashes, and incomplete evidence can never be accepted by
> baseline.

ckdn keeps three **independent** axes rather than collapsing them into one
pass/fail (which is exactly how false-green creeps in):

| axis          | values                                    | meaning                                   |
|---------------|-------------------------------------------|-------------------------------------------|
| **execution** | `pass` / `fail` / `error` / `parse_mismatch` | the run's real [status](status-model.md); baseline **never** touches it |
| **findings**  | `baseline.known` / `baseline.new`          | how the findings classify against the baseline |
| **gate**      | `pass` / `fail` / `unavailable`            | the CI policy decision, reported separately |

A digest with a baseline active:

```json
{
  "check": "ruff",
  "status": "fail",
  "rc": 1,
  "findings": [{ "id": "F401", "kind": "lint", "message": "…", "baselined": true }],
  "baseline": { "known": 1, "new": 0 },
  "gate": { "status": "pass", "policy": "no_new_findings" }
}
```

The human sees the truth — the tool returned red — while CI may still pass
because there are no regressions.

## The gate's trust rules

The gate may accept a nonzero exit **only** when the evidence is trustworthy.
It reports `unavailable` (and CI falls back to the honest execution exit) unless
all of these hold:

1. the exit code means findings, not an infra failure;
2. the parser understood the output (`parser_ok`);
3. every finding was classified against the baseline;
4. there are no new findings;
5. no `parse_mismatch`, crash, timeout, or unknown failure.

Baseline never masks an unknown failure.

## Usage

Point `ckdn.toml` at a baseline file, record the current state, then gate:

```toml
[run]
baseline = "ckdn.baseline.json"
```

```bash
ckdn baseline ruff          # run ruff, record its findings as accepted
ckdn run ruff --gate        # exit reflects the gate: 0 while no NEW findings
ckdn run ruff               # no --gate: honest execution exit (still red)
```

- `ckdn baseline <check>` runs the check and writes every finding's fingerprint
  to the baseline file (members of an alias are recorded individually).
  Fingerprints ignore line/column, so findings survive code moving within a
  file.
- `ckdn run <check> --gate` makes the **process exit** reflect the gate (for
  CI); the digest's `status` stays the honest execution truth. Works for
  `--all` too — the aggregate gate is `unavailable` > `fail` > `pass` across
  members.

Commit the baseline file; shrink it as you fix pre-existing findings.
