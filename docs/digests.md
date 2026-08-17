---
icon: lucide/file-json
---

# Digests & schemas

Stdout and on-disk `digest.json` are **compact** and **sparse**: absent keys
mean empty / `0` / `false`. Always present: `schema`, `check`, `status`, `rc`,
`run_dir`.

Green pass (intentionally tiny):

```json
{
  "schema": "ckdn.digest/2",
  "check": "ruff",
  "status": "pass",
  "rc": 0,
  "run_dir": ".agent-runs/20260707T101500Z-ruff"
}
```

Failure keeps the evidence — bounded findings with locations and snippets,
gates, notes, and explicit truncation counters:

```json
{
  "schema": "ckdn.digest/2",
  "check": "pytest",
  "status": "fail",
  "status_reason": "exit code 1 with 1 finding(s)",
  "rc": 1,
  "summary": { "counts": { "tests": 214, "failures": 1, "skipped": 2 } },
  "findings_total": 1,
  "findings": [
    {
      "id": "tests.test_digest::test_sparse_keys",
      "kind": "test_failure",
      "message": "assert 'notes' not in digest",
      "location": "tests/test_digest.py:41",
      "detail": ["E       AssertionError: assert 'notes' not in digest"]
    }
  ],
  "run_dir": ".agent-runs/20260707T101500Z-pytest",
  "artifacts": ["full.log", "junit.xml", "meta.json"]
}
```

`log_tail` is a bounded slice of the end of `full.log`. It is attached on
`error` and `parse_mismatch` — and also on a plain `fail`: unconditionally when
the parser never promised findings (`generic`), and whenever a parser asks for
it — of the built-ins, only `generic` ever does. Do not infer a status from its
presence; read `status`.

With a [baseline](baselines.md) configured, `gate` — the CI decision, reported
beside `status` and never in place of it — is attached to **every** run,
including a green `pass`. `baseline` (the `known` / `new` counts) is attached
only when the run produced findings to classify, so a red `gate` with no
`baseline` beside it is the ordinary shape of a failure that had nothing to
classify.

`digest.json` carries **facts only**; policy belongs in a skill or `CLAUDE.md`,
not in the data file. Provenance (timestamps, durations, exact argv, log
hash, ckdn version) lives in a sibling `meta.json` (`ckdn.meta/1`).

## Every key

The schema sets `additionalProperties: false`, so this list is the whole
document — a key not below is a key ckdn does not emit.

| key                  | present when                                                     |
|----------------------|------------------------------------------------------------------|
| `schema`             | always — `ckdn.digest/2`                                          |
| `check`              | always                                                            |
| `status`             | always — `pass` / `fail` / `error` / `parse_mismatch`             |
| `rc`                 | always; the tool's real code, including a negative signal code    |
| `run_dir`            | always; the run directory, forward slashes                        |
| `status_reason`      | every non-`pass` status, and never on `pass`                      |
| `summary`            | the parser produced counts (after empty values are pruned)        |
| `findings_total`     | ≥ 1 finding — the full count, before `[run].top` is applied       |
| `findings`           | ≥ 1 finding — at most `top` of them                               |
| `findings_truncated` | more findings than `top`: how many are not shown                  |
| `gate_failures`      | a config-level gate did not hold: coverage `fail_under`, pylint `score_fail_under` |
| `notes`              | the run has something to say: a reclaimed lock, a timeout, a parser that could not read its artifact |
| `artifacts`          | non-`pass` and the run directory holds files to inspect           |
| `log_tail`           | see above                                                         |
| `timed_out`          | `true` when the check hit its `timeout` (absent means false)      |
| `interrupted`        | `true` when the run was cut short by SIGINT (Ctrl-C)              |
| `baseline`           | a baseline is configured **and** the run produced findings        |
| `gate`               | a baseline is configured                                          |

A finding carries `id`, `kind` and `message`; `location` and `detail` when the
parser has them, and `baselined: true` when the baseline recognized it.

## The JSON Schema contract

Every document ckdn writes declares a `schema` id. Three of them have a formal
JSON Schema (Draft 2020-12) shipped inside the wheel under `ckdn/schemas/`:

- `ckdn.digest/2` — a single atomic check's digest
- `ckdn.aggregate/1` — an [alias aggregate](aliases.md)
- `ckdn.meta/1` — per-run provenance

The fourth, `ckdn.baseline/1` (the [baseline](baselines.md) file), has no
packaged schema: `ckdn schema` does not list it and `load_schema` raises for
it. It is ckdn's own state rather than output for a consumer to validate.

Downstream consumers can validate ckdn output against these schemas, and
ckdn's own test suite builds every status variant and validates it against
them — so a structural drift fails CI.

Print a schema from the CLI (pipe it into your own validation), or list the
ids:

```bash
ckdn schema ckdn.digest/2      # print one schema
ckdn schema                    # list schema ids
```

Or load one in Python (stdlib-only, the core keeps its zero-dependency
guarantee):

```python
from ckdn.schema import load_schema

schema = load_schema("ckdn.digest/2")
```

## Run directory

```
.agent-runs/
  20260707T101500Z-ruff/
    full.log      # interleaved stdout+stderr
    ruff.json     # tool artifact via {run_dir}
    meta.json     # argv, rc, timestamps, duration, log sha256
    digest.json   # deterministic facts for the reader
  .locks/         # one advisory lock file per check, not a run
  latest -> 20260707T101500Z-ruff
  LATEST          # instead of `latest` where symlinks are unavailable
```

Dot-prefixed entries are bookkeeping and are never read as runs. `.locks/`
holds one file per check, which is how a second concurrent run of the same
check is refused rather than doubling the load on the same tool. `LATEST` is a
plain file naming the newest run directory; ckdn writes it in place of the
`latest` symlink where symlinks are unavailable — always on Windows, where
creating one needs privilege — and removes a stale marker once a symlink works
again. The reverse is not cleaned up: if symlink creation starts failing after
one succeeded, a stale `latest` is left beside the new `LATEST`, and `latest`
wins, so a reader gets the older run.

`.agent-runs/` is evidence: do not edit it; keep it out of version control.
