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
from ckdn.runner import LOG_NAME, list_run_dirs, resolve_run_dir

DEFAULT_EVIDENCE_LIMIT = 200
MAX_EVIDENCE_LIMIT = 2000

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


def get_digest(cfg: Config, ref: str | None = None) -> dict[str, Any]:
    """Load ``digest.json`` for ``ref`` or the latest run."""
    run_dir = resolve_run_dir(cfg.runs_dir, ref)
    if run_dir is None:
        raise RunNotFoundError("no matching run found (nothing has been run yet?)")
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


def list_runs(cfg: Config, *, limit: int = 10) -> list[dict[str, Any]]:
    """Return recent run summaries (oldest→newest within the window)."""
    n = max(0, limit)
    dirs = list_run_dirs(cfg.runs_dir)[-n:]
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
    target = (run_dir / artifact).resolve()
    root = run_dir.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
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
        raise RunNotFoundError("no matching run found (nothing has been run yet?)")

    digest = get_digest(cfg, run_dir.name)
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
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    sliced = lines[line_offset : line_offset + line_limit]
    payload["artifact"] = {
        "name": artifact,
        "offset": line_offset,
        "limit": line_limit,
        "total_lines": len(lines),
        "truncated": line_offset + line_limit < len(lines),
        "lines": sliced,
    }
    return payload
