# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Digest and meta document construction.

Split of concerns:

* ``digest.json`` -- deterministic facts for the agent. Given identical tool
  output and an identical run directory path, byte-identical output. No
  timestamps, no durations, no environment noise.
* ``meta.json``   -- provenance: when, how long, exact argv, log hash,
  ckdn version. Everything nondeterministic lives here.

Digests carry no policy. "What to do about a failure" belongs to the skill
or CLAUDE.md, not to a data file.

Schema ``ckdn.digest/2`` is sparse: absent keys mean empty / ``0`` / ``false``.
Always present: ``schema``, ``check``, ``status``, ``rc``, ``run_dir``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ckdn import AGGREGATE_SCHEMA, DIGEST_SCHEMA, META_SCHEMA, __version__
from ckdn.parsers.base import ParseResult
from ckdn.runner import RunOutcome

DIGEST_NAME = "digest.json"
META_NAME = "meta.json"


def dump_json(data: dict[str, Any]) -> str:
    """Compact deterministic JSON (agent / on-disk default)."""
    return (
        json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def dump_json_pretty(data: dict[str, Any]) -> str:
    """Indented JSON for human ``ckdn show``."""
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def tail(text: str, n: int) -> list[str]:
    if n <= 0:
        return []
    return text.splitlines()[-n:]


def prune_summary(value: Any) -> Any:
    """Drop empty containers and numeric zeros; keep bools and non-empty text."""
    if isinstance(value, dict):
        pruned = {
            k: v
            for k, v in ((k, prune_summary(v)) for k, v in value.items())
            if v is not None
        }
        return pruned or None
    if isinstance(value, list):
        kept = [v for v in (prune_summary(item) for item in value) if v is not None]
        return kept or None
    if value is None or value == "" or value == 0 or value == 0.0:
        return None
    if value is False:
        return None
    return value


def build_digest(
    *,
    check: str,
    status: str,
    reason: str,
    outcome: RunOutcome,
    result: ParseResult,
    run_dir_rel: str,
    top: int,
    include_tail: bool,
    tail_lines: int,
    artifacts: list[str],
) -> dict[str, Any]:
    shown = [f.to_dict() for f in result.findings[:top]]
    total = len(result.findings)
    truncated = max(0, total - len(shown))

    digest: dict[str, Any] = {
        "schema": DIGEST_SCHEMA,
        "check": check,
        "status": status,
        "rc": outcome.rc,
        "run_dir": run_dir_rel,
    }
    if status != "pass":
        digest["status_reason"] = reason
    if outcome.timed_out:
        digest["timed_out"] = True
    if outcome.interrupted:
        # Why the process ended, like timed_out -- not a result of its own.
        digest["interrupted"] = True

    summary = prune_summary(result.summary)
    if isinstance(summary, dict) and summary:
        digest["summary"] = summary

    if result.gate_failures:
        digest["gate_failures"] = list(result.gate_failures)
    if result.notes:
        digest["notes"] = list(result.notes)

    if total > 0:
        digest["findings_total"] = total
        digest["findings"] = shown
        if truncated > 0:
            digest["findings_truncated"] = truncated

    if status != "pass" and artifacts:
        digest["artifacts"] = artifacts
    if include_tail:
        digest["log_tail"] = tail(outcome.log_text, tail_lines)
    return digest


def build_alias_aggregate(
    *,
    alias: str,
    results: list[tuple[str, str, int, str]],
    status: str,
    rc: int,
) -> dict[str, Any]:
    """Sparse aggregate for alias stdout (members already have digests on disk).

    ``rc`` mirrors the process exit code (the pass-through of the first
    non-green member), so the stdout document is self-contained. Each member's
    ``run_dir`` is the same relative, posix path its own digest reports.
    """
    members: list[dict[str, Any]] = []
    for check, member_status, member_rc, run_dir in results:
        row: dict[str, Any] = {
            "check": check,
            "status": member_status,
            "rc": member_rc,
        }
        if member_status != "pass":
            row["run_dir"] = run_dir
        members.append(row)
    return {
        "schema": AGGREGATE_SCHEMA,
        "alias": alias,
        "status": status,
        "rc": rc,
        "members": members,
    }


def build_meta(*, check: str, parser: str, outcome: RunOutcome) -> dict[str, Any]:
    log_bytes = outcome.log_text.encode("utf-8", errors="replace")
    return {
        "schema": META_SCHEMA,
        "ckdn_version": __version__,
        "check": check,
        "parser": parser,
        "command": outcome.tokens,
        "rc": outcome.rc,
        "timed_out": outcome.timed_out,
        "started_at": outcome.started_at,
        "duration_s": outcome.duration_s,
        "log_sha256": hashlib.sha256(log_bytes).hexdigest(),
        "log_bytes": len(log_bytes),
    }


def write_documents(
    run_dir: Path, digest: dict[str, Any], meta: dict[str, Any]
) -> None:
    (run_dir / META_NAME).write_text(dump_json(meta), encoding="utf-8")
    (run_dir / DIGEST_NAME).write_text(dump_json(digest), encoding="utf-8")


def list_artifacts(run_dir: Path) -> list[str]:
    """Names of files a reader may inspect in the run directory.

    Excludes digest.json itself (it is the document being built).
    """
    return sorted(
        p.name for p in run_dir.iterdir() if p.is_file() and p.name != DIGEST_NAME
    )
