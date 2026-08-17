---
icon: lucide/traffic-cone
---

# Status model

Every run reconciles the exit code (`rc`) against the parser into exactly one
status. **`pass` is the only green state.**

| rc  | parser                                     | status           | meaning                          |
|-----|--------------------------------------------|------------------|----------------------------------|
| 0   | confident, no findings, gates ok           | `pass`           | green                            |
| 0   | gate failed (e.g. coverage < `fail_under`) | `fail`           | tool happy, policy not           |
| ≠ 0 | findings extracted                         | `fail`           | normal red + evidence            |
| ≠ 0 | no findings, none expected (`generic`)     | `fail`           | the exit code *is* the evidence  |
| ≠ 0 | no findings, evidence expected             | `error`          | infra / collection — fix the run |
| ≠ 0 | could not interpret output                 | `error`          | same, with log tail              |
| 0   | findings anyway / unreadable               | `parse_mismatch` | green untrusted                  |

The two `≠ 0, no findings` rows differ only in what the parser promised. A
parser that never produces findings declares `evidence_expected=False` — the
`generic` parser, for builds, deploys and scripts — so its silence is not a
collection failure and the run is a plain `fail`. Every other parser owes
evidence for a red exit, and its absence is the failure.

Invariants (enforced by `ckdn.reconcile`, covered by contract tests):

- Text never upgrades a nonzero exit code to green.
- A zero exit code never survives contradicting evidence.
- A confused parser sets `parser_ok=false` → loud `error` / `parse_mismatch`,
  never a silent clean.

## Exit-code contract

`ckdn run` exits with the original command's code, so it drops into any hook or
CI slot where the raw command used to be. Two extra rules: `rc == 0` with a
non-green status exits `1`, and a code outside `1–255` is **replaced** by `1`
rather than folded into range — `rc = 300` exits `1`, and so does the negative
code a signal-killed child reports (`-11` after `SIGSEGV`). The digest keeps the
real number in `rc` either way; only the process exit is constrained.

`--gate` replaces this contract wholesale. The exit becomes the
[baseline gate](baselines.md) decision: `pass` → `0`, `fail` → `1`,
`unavailable` → the honest execution exit above. A run without a gate — no
`[run].baseline` configured — also falls back to that exit, so adding `--gate`
to a project that has no baseline changes nothing.

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

The child is detached into its own process group (POSIX) or held in a job
object (Windows), and **the whole tree** — not just the direct child — is
stopped: asked to finish, given a grace period, then terminated
outright. That is `SIGTERM` → grace → `SIGKILL` over the process group on
POSIX, and `CTRL_BREAK` → grace → the job object on Windows. The group or job
is what holds a wrapper's children when the wrapper itself exits first. The
tree is stopped on every path, including a clean exit, so a check cannot leave
a background process appending to a log whose digest is sealed.

What the "ask" buys differs by platform, and it is worth being exact. On POSIX
a tool that ignores `SIGTERM` keeps running until the grace expires. On
Windows the *default* `CTRL_BREAK` handler exits the process at once, so only
a tool that installs its own handler gets a shutdown window — for everything
else the ask and the kill look the same. The grace is an opportunity, not a
promise that a report will be finished.

On Windows the tree is held by a job object rather than by parentage, so a
grandchild whose parent already exited is still caught. Both halves are best
effort: if the job cannot be created the run falls back to `taskkill`, which
is blind to re-parenting.

Limits, stated rather than papered over:

- **POSIX:** a check that detaches into a **new session** of its own leaves
  ckdn's group and outlives the run. Nothing portable prevents that. A job is
  harder to leave — breaking out of one takes a flag the job does not grant.
- **POSIX:** `kill -9` on ckdn runs no cleanup, so its tree survives. On
  Windows the job dies with ckdn's handle, so that case is covered *when a
  job was created*; where it fell back to `taskkill`, nothing runs either.
- **Windows:** descendants the child creates before it is placed in the job
  are outside it. The window is milliseconds and cannot be closed from
  Python — a plain subprocess cannot be started suspended.

After any of these, the next run of that check reclaims the lock and says so
in its notes. ckdn never kills anything it cannot prove it owns: only its own
pid is recorded, and pids get recycled.

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
