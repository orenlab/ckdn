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

**A run that was cut short outranks every other signal**, whether by Ctrl-C or
by its own timeout. It produced partial evidence, and partial evidence is
never a verdict: a half-written report does not become `fail`, and an
unreadable one does not become `parse_mismatch` — both are `error`. A killed
tool's findings describe the moment it died, not the code. Consumers that
predate these fields simply see `error`; an absent field means `false`.

An alias or `--all` series stops at an interrupted member rather than starting
the next one. Its aggregate carries `interrupted: true` and exits `130` — the
interruption outranks an earlier red member's exit code, which would otherwise
report the series' verdict and hide that the rest never ran.

### What "terminated" guarantees

On POSIX the child starts in its own process group and **the group** — not
just the direct child — is terminated: `SIGTERM`, a grace period, then
`SIGKILL` for whatever is still there. This is what makes the guarantee hold
when a wrapper like `uv` exits promptly on `SIGTERM` while the tool it
launched ignores it. The group is terminated on every path, including a clean
exit, so a check cannot leave a background process appending to a log whose
digest is sealed.

Three limits, stated rather than papered over:

- **Windows has no graceful phase.** The tree is terminated forcefully at once
  (`taskkill /T /F`), so a tool gets none of the grace a POSIX run gives it to
  finish writing its report — the same `timeout` can therefore yield a
  different digest on the two platforms. `taskkill` also walks parent links,
  so a grandchild whose parent already exited is missed.

- A check that deliberately detaches into a **new session** of its own leaves
  ckdn's group and outlives the run. Nothing portable can prevent that.
- `kill -9` on ckdn itself runs no cleanup, so its tree survives. The next run
  of that check reclaims the lock and says so in its notes; ckdn never kills
  anything it cannot prove it owns, because only its own pid is recorded and
  pids get recycled.

The log streams straight to `full.log`, so an interrupted run still leaves the
output it managed to produce, and `meta.json` records the sha256 of those
bytes exactly as they sit on disk.

For an alias, the exit code is the aggregate `rc`; see
[Aliases & aggregates](aliases.md).

## Determinism

`digest.json` is deterministic: given identical tool output and an identical
run-directory path, ckdn writes byte-identical JSON (keys sorted, no
timestamps or durations — those live in `meta.json`). Paths are normalized to
forward slashes so a digest is byte-stable across operating systems. This is
guarded by tests, so a regression fails CI.
