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
  "schema": "ckdn.digest/2",
  "check": "pytest",
  "status": "fail",
  "status_reason": "exit code 1 with 1 finding(s)",
  "rc": 1,
  "summary": { "counts": { "tests": 2, "failures": 1 } },
  "findings_total": 1,
  "findings": [
    {
      "id": "tests.test_math::test_retry_on_429",
      "kind": "test_failure",
      "message": "NotImplementedError: retry policy not written yet",
      "detail": ["E       NotImplementedError: retry policy not written yet"],
      "baselined": true
    }
  ],
  "run_dir": ".agent-runs/20260707T101500Z-pytest",
  "artifacts": ["full.log", "junit.xml", "meta.json"],
  "baseline": { "known": 1, "new": 0 },
  "gate": { "status": "pass", "policy": "no_new_findings" }
}
```

The human sees the truth — the tool returned red — while CI may still pass
because there are no regressions. `baselined` marks the individual findings the
baseline recognized; it appears only on findings the digest actually shows, so
`baseline.known` is the count to trust, not the number of marks.

## The gate's trust rules

The gate is derived on every run once `[run].baseline` is set, and the first
rule that matches wins:

1. **Untrusted evidence → `unavailable`.** The parser could not interpret the
   output (`parser_ok` false), or the run reconciled to `error` or
   `parse_mismatch` — which is also where an interrupted or timed-out run
   lands. Nothing was classified, so nothing can be accepted.
2. **A policy gate breached → `fail`.** A coverage `fail_under` miss, or a
   pylint score below its floor, lands in `gate_failures`, not in `findings`.
   It has no finding, therefore no fingerprint, therefore no way into a
   baseline file: **a policy gate can never be baselined**, and the run stays
   red however old the breach is.
3. **Any new finding → `fail`.**
4. **A red run that classified nothing → `unavailable`.** Execution was not
   `pass`, and the baseline saw neither new findings nor known ones — an
   rc-only `generic` check that simply exited nonzero has no findings at all.
   "No new findings" would be a lie here: there were never any to compare.
5. Otherwise → **`pass`.**

Rules 2 and 4 are why "no new findings" is not the same test as
`new == 0`. A failure that produced nothing to classify is an unknown failure,
and baseline never masks one.

Under `--gate` the verdict becomes the process exit: `pass` → `0`, `fail` →
`1`, and `unavailable` hands the exit back to the honest execution exit — the
gate declines to answer rather than inventing one.

Every gate carries `status` and `policy`. A gate that is not `pass` also carries
the `reason` it landed there — except an aggregate's, which reports only the
worst member's verdict and sends you to the member digests for the why.

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
  to the baseline file (members of an alias are recorded individually). The run
  itself is an ordinary one: its `digest.json` is capped by `[run].top` like any
  other, while the baseline file records **every** finding — the complete set
  travels beside the digest rather than inside it.
- `ckdn run <check> --gate` makes the **process exit** reflect the gate (for
  CI); the digest's `status` stays the honest execution truth. Works for
  `--all` too — the aggregate gate is `unavailable` > `fail` > `pass` across
  members.

Commit the baseline file; shrink it as you fix pre-existing findings. It
declares `schema: "ckdn.baseline/1"` and, unlike a digest, has no packaged JSON
Schema — see [Digests & schemas](digests.md).

## What a fingerprint is

`sha256(check ␀ kind ␀ path ␀ message)` truncated to 16 hex characters, where
`path` is the finding's `location` with any trailing `:line[:col]` removed.
That last step is the point — findings survive code moving within a file — and
the rest follows from what is left in the hash:

- **The finding's `id` is not in it.** A ruff rule code or a pytest node id can
  change without the entry going stale; equally, an entry cannot be read back
  as "F401 is accepted here".
- **The whole `message` is.** Reword it — a tool upgrade that rephrases its
  diagnostic — and the same defect comes back `new`. That is the price of
  line-drift tolerance: once positions are dropped, text is the only stable
  part left.
- **Findings agreeing on kind, path and message collapse into one entry.**
  Twelve identical unused-import messages in one file record as one
  fingerprint, and a finding carrying no `location` is keyed by its kind and
  message across the entire check.

## A missing or unreadable baseline file

A baseline file that does not exist is read as an **empty** baseline: no error,
every finding classified `new`, so `--gate` exits 1 on any run that has
findings. A typo in `[run].baseline` therefore fails closed — CI goes red
rather than quietly accepting everything.

Within a file that is valid JSON, entries ckdn cannot use are skipped in
silence: a check whose value is not a list of fingerprints simply loses its
baseline, and all of that check's findings come back `new` on the next run. A
file that is not valid JSON at all is not handled — the run aborts once the
check has already finished, leaving a run directory with `full.log`, the
tool's artifacts and `meta.json`, but no `digest.json`.

## What `ckdn baseline` will not do

- It **refuses** a run that was interrupted, or that finished `error` or
  `parse_mismatch`: exit `2`, baseline file unchanged. The gate's first rule
  again — an untrusted or partial result is no basis for accepting findings,
  and recording the empty set it produced would mark every real finding `new`
  on the next run.
- It **replaces** the named check's entry rather than merging into it, so
  fingerprints the run no longer produces are dropped. That is how the file
  shrinks as you fix things.
- It **keeps** every entry for a check you did not name — including checks
  deleted from `ckdn.toml` since. Those linger indefinitely with no staleness
  warning; prune them by hand.
