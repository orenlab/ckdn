---
icon: lucide/traffic-cone
---

# Status model

Every run reconciles the exit code (`rc`) against the parser into exactly one
status. **`pass` is the only green state.**

| rc  | parser                                    | status           | meaning                          |
|-----|-------------------------------------------|------------------|----------------------------------|
| 0   | confident, no findings, gates ok          | `pass`           | green                            |
| 0   | gate failed (e.g. coverage < `fail_under`) | `fail`           | tool happy, policy not           |
| ≠ 0 | findings extracted                        | `fail`           | normal red + evidence            |
| ≠ 0 | no findings, evidence expected            | `error`          | infra / collection — fix the run |
| ≠ 0 | could not interpret output                | `error`          | same, with log tail              |
| 0   | findings anyway / unreadable              | `parse_mismatch` | green untrusted                  |

Invariants (enforced by `ckdn.reconcile`, covered by contract tests):

- Text never upgrades a nonzero exit code to green.
- A zero exit code never survives contradicting evidence.
- A confused parser sets `parser_ok=false` → loud `error` / `parse_mismatch`,
  never a silent clean.

## Exit-code contract

`ckdn run` exits with the original command's code (clamped 1–255), so it drops
into any hook or CI slot where the raw command used to be. One extra rule:
`rc == 0` with a non-green status exits `1`.

When ckdn owns the failure it uses conventional synthetic codes — `124`
timeout, `126` blocked by command policy, `127` command not found, `130`
interrupted (Ctrl-C) — each also reconciling to a non-green status with
evidence.

## How a run ends

`timed_out` and `interrupted` describe **why the process stopped**; they are
not results of their own, so the status stays inside the four-value model:

| ending | rc | flags | status |
|--------|-----|-------|--------|
| timeout | `124` | `timed_out: true` | `error` |
| Ctrl-C | `130` | `interrupted: true` | `error` |

**Interruption outranks every other signal.** A run cut short produced partial
evidence, and partial evidence is never a verdict: a half-written report does
not become `fail`, and an unreadable one does not become `parse_mismatch` —
both are `error` with `interrupted: true`. Consumers that predate the field
simply see `error`; an absent field means `false`.

ckdn also guarantees the run leaves nothing behind. The child starts in its
own process group and the whole tree (`uv` → `pytest` → workers) is terminated
on timeout or interrupt — `SIGTERM`, a grace period, then `SIGKILL`. The log
streams straight to `full.log`, so an interrupted run still leaves the output
it managed to produce.

For an alias, the exit code is the aggregate `rc`; see
[Aliases & aggregates](aliases.md).

## Determinism

`digest.json` is deterministic: given identical tool output and an identical
run-directory path, ckdn writes byte-identical JSON (keys sorted, no
timestamps or durations — those live in `meta.json`). Paths are normalized to
forward slashes so a digest is byte-stable across operating systems. This is
guarded by tests, so a regression fails CI.
