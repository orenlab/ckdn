# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Read-side application queries over config and run artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ckdn.app.errors import ArtifactError, DigestError, RunNotFoundError
from ckdn.config import Config
from ckdn.digest import DIGEST_NAME, META_NAME, list_artifacts
from ckdn.parsers.base import ArtifactPathError, resolve_under_run_dir
from ckdn.runner import LOG_NAME, list_run_dirs, resolve_run_dir

DEFAULT_EVIDENCE_LIMIT = 200
MAX_EVIDENCE_LIMIT = 2000
MAX_LIST_RUNS_LIMIT = 500

# Streaming artifact reads: never hold the whole file in memory. Scan in fixed
# chunks and cap the bytes retained per returned line so a single pathological
# (e.g. newline-free) artifact cannot exhaust memory via get_evidence.
_EVIDENCE_READ_CHUNK = 1 << 16  # 64 KiB
_MAX_EVIDENCE_LINE_BYTES = 64 << 10  # 64 KiB per returned line


def _no_run_error(ref: str | None) -> RunNotFoundError:
    """Distinguish an unresolved/invalid ref from an empty runs directory.

    A truthy ``ref`` that resolves to nothing is either unknown or not a valid
    run id inside the runs dir (absolute/``..``/multi-segment/symlink refs are
    refused by ``resolve_run_dir``); say so rather than implying nothing has run.
    """
    if ref:
        return RunNotFoundError(
            f"no run matching {ref!r} (unknown, or not a valid run id "
            "inside the runs directory)"
        )
    return RunNotFoundError("no matching run found (nothing has been run yet?)")


_DIGEST_EVIDENCE_KEYS = (
    "findings",
    "findings_total",
    "findings_truncated",
    "gate_failures",
    "notes",
    "log_tail",
    "summary",
    "status_reason",
    "artifacts",
)


def list_checks(cfg: Config) -> list[dict[str, Any]]:
    """Return sorted check metadata for agents / MCP."""
    out: list[dict[str, Any]] = []
    for name in sorted(cfg.checks):
        check = cfg.checks[name]
        if check.is_alias:
            out.append(
                {
                    "name": name,
                    "kind": "alias",
                    "members": list(check.members or ()),
                    "fail_fast": check.fail_fast,
                }
            )
        else:
            item: dict[str, Any] = {
                "name": name,
                "kind": "atomic",
                "parser": check.parser,
                "command": check.command,
            }
            if check.timeout is not None:
                item["timeout"] = check.timeout
            if check.options:
                item["options"] = dict(check.options)
            out.append(item)
    return out


def _load_digest(run_dir: Path) -> dict[str, Any]:
    """Load and validate ``digest.json`` from an already-resolved run dir.

    Reading is anchored to ``run_dir`` itself -- never re-resolved by basename
    -- so a run's digest can never be paired with another run's artifacts.
    """
    digest_path = run_dir / DIGEST_NAME
    if not digest_path.exists():
        raise DigestError(f"run {run_dir.name} has no {DIGEST_NAME}")
    try:
        doc = json.loads(digest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DigestError(f"run {run_dir.name} has corrupt {DIGEST_NAME}") from exc
    if not isinstance(doc, dict):
        raise DigestError(f"run {run_dir.name} digest root is not an object")
    return doc


def get_digest(cfg: Config, ref: str | None = None) -> dict[str, Any]:
    """Load ``digest.json`` for ``ref`` or the latest run."""
    run_dir = resolve_run_dir(cfg.runs_dir, ref)
    if run_dir is None:
        raise _no_run_error(ref)
    return _load_digest(run_dir)


def list_runs(cfg: Config, *, limit: int = 10) -> list[dict[str, Any]]:
    """Return recent run summaries (oldest→newest within the window)."""
    n = min(max(0, limit), MAX_LIST_RUNS_LIMIT)
    dirs = list_run_dirs(cfg.runs_dir)[-n:] if n else []
    rows: list[dict[str, Any]] = []
    for run_dir in dirs:
        row: dict[str, Any] = {"run_id": run_dir.name, "check": "?", "status": "?"}
        digest_path = run_dir / DIGEST_NAME
        if digest_path.exists():
            try:
                doc = json.loads(digest_path.read_text(encoding="utf-8"))
                if isinstance(doc, dict):
                    row["check"] = str(doc.get("check", "?"))
                    row["status"] = str(doc.get("status", "?"))
                    if "rc" in doc:
                        row["rc"] = doc["rc"]
                    if "run_dir" in doc:
                        row["run_dir"] = doc["run_dir"]
            except json.JSONDecodeError:
                row["status"] = "corrupt"
        rows.append(row)
    return rows


def _safe_artifact_path(run_dir: Path, artifact: str) -> Path:
    if not artifact or artifact.strip() != artifact:
        raise ArtifactError(f"invalid artifact name: {artifact!r}")
    if Path(artifact).is_absolute() or ".." in Path(artifact).parts:
        raise ArtifactError(f"artifact path escapes run directory: {artifact!r}")
    allowed = set(list_artifacts(run_dir))
    # full.log is always an evidence candidate even if list_artifacts filters
    allowed.add(LOG_NAME)
    allowed.add(DIGEST_NAME)
    allowed.add(META_NAME)
    if artifact not in allowed:
        raise ArtifactError(
            f"artifact '{artifact}' not found in run {run_dir.name}; "
            f"available: {', '.join(sorted(allowed))}"
        )
    try:
        target = resolve_under_run_dir(run_dir, run_dir / artifact)
    except ArtifactPathError as exc:
        raise ArtifactError(
            f"artifact path escapes run directory: {artifact!r}"
        ) from exc
    if not target.is_file():
        raise ArtifactError(
            f"artifact '{artifact}' is not a file in run {run_dir.name}"
        )
    return target


def get_evidence(
    cfg: Config,
    *,
    ref: str | None = None,
    artifact: str | None = None,
    offset: int = 0,
    limit: int = DEFAULT_EVIDENCE_LIMIT,
    include_meta: bool = False,
) -> dict[str, Any]:
    """Return bounded digest evidence and/or a sliced artifact body.

    When ``artifact`` is omitted, returns digest evidence fields and the
    artifact index — never the full log body.
    """
    run_dir = resolve_run_dir(cfg.runs_dir, ref)
    if run_dir is None:
        raise _no_run_error(ref)

    digest = _load_digest(run_dir)
    payload: dict[str, Any] = {
        "run_id": run_dir.name,
        "check": digest.get("check"),
        "status": digest.get("status"),
        "rc": digest.get("rc"),
        "run_dir": digest.get("run_dir"),
        "artifacts": list_artifacts(run_dir),
    }
    for key in _DIGEST_EVIDENCE_KEYS:
        if key in digest and key != "artifacts":
            payload[key] = digest[key]

    if include_meta:
        meta_path = run_dir / META_NAME
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(meta, dict):
                    payload["meta"] = meta
            except json.JSONDecodeError:
                payload["meta_error"] = f"corrupt {META_NAME}"

    if artifact is None:
        return payload

    line_offset = max(0, offset)
    line_limit = min(max(1, limit), MAX_EVIDENCE_LIMIT)
    path = _safe_artifact_path(run_dir, artifact)
    sliced, total_lines = _slice_artifact_lines(path, line_offset, line_limit)
    payload["artifact"] = {
        "name": artifact,
        "offset": line_offset,
        "limit": line_limit,
        "total_lines": total_lines,
        "truncated": line_offset + line_limit < total_lines,
        "lines": sliced,
    }
    return payload


def _slice_artifact_lines(path: Path, offset: int, limit: int) -> tuple[list[str], int]:
    """Stream ``path`` and return ``(window, total_lines)``.

    Only the ``[offset, offset + limit)`` window is retained, and each retained
    line is capped at ``_MAX_EVIDENCE_LINE_BYTES``, so neither a huge file nor a
    single unbounded line can be pulled into memory in full. Line splitting
    matches ``str.splitlines`` for ``\\n`` (and trims a trailing ``\\r`` so CRLF
    logs read cleanly).
    """
    end = offset + limit
    window: list[bytes] = []
    total = 0
    idx = 0
    cur = bytearray()
    line_nonempty = False
    keeping = offset <= idx < end

    with path.open("rb") as fh:
        while chunk := fh.read(_EVIDENCE_READ_CHUNK):
            pos = 0
            size = len(chunk)
            while pos < size:
                nl = chunk.find(b"\n", pos)
                seg_end = size if nl == -1 else nl
                if seg_end > pos:
                    line_nonempty = True
                    if keeping and len(cur) < _MAX_EVIDENCE_LINE_BYTES:
                        room = _MAX_EVIDENCE_LINE_BYTES - len(cur)
                        cur += chunk[pos : min(seg_end, pos + room)]
                if nl == -1:
                    break
                total += 1
                if keeping:
                    window.append(bytes(cur))
                idx += 1
                cur = bytearray()
                line_nonempty = False
                keeping = offset <= idx < end
                pos = nl + 1

    # A trailing segment with no terminating newline is a final line.
    if line_nonempty:
        total += 1
        if keeping:
            window.append(bytes(cur))

    decoded = [b.decode("utf-8", errors="replace").removesuffix("\r") for b in window]
    return decoded, total
