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

On `error` / `parse_mismatch` the digest additionally carries a bounded
`log_tail`.

`digest.json` carries **facts only**; policy belongs in a skill or `CLAUDE.md`,
not in the data file. Provenance (timestamps, durations, exact argv, log
hash, ckdn version) lives in a sibling `meta.json` (`ckdn.meta/1`).

## The JSON Schema contract

Every document ckdn writes declares a `schema` id, and each id has a formal
JSON Schema (Draft 2020-12) shipped inside the wheel under `ckdn/schemas/`:

- `ckdn.digest/2` — a single atomic check's digest
- `ckdn.aggregate/1` — an [alias aggregate](aliases.md)
- `ckdn.meta/1` — per-run provenance

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
  latest -> 20260707T101500Z-ruff
```

`.agent-runs/` is evidence: do not edit it; keep it out of version control.
