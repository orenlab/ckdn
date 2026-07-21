# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Finding baselines — classify findings as known/new and derive a gate.

Baseline **never changes execution truth**. A nonzero tool result stays
``fail`` in the digest. Baseline classifies recognized findings as *known* or
*new* against a stored set of accepted fingerprints, and derives a separate
``gate`` decision. The gate may accept a nonzero exit for CI **only** when the
evidence is trustworthy: the parser understood the output and there are no new
findings. Anything else — ``error``, ``parse_mismatch``, a crash — is
``unavailable``; baseline never masks an unknown failure.

Three independent axes, reported separately: execution (the digest ``status``),
findings (``baseline.known`` / ``baseline.new``), and the ``gate``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

#: Baseline document schema identifier.
BASELINE_SCHEMA = "ckdn.baseline/1"


def _path_of(location: str | None) -> str:
    """A finding's file path with any trailing ``:line[:col]`` stripped.

    Keeps a fingerprint stable when code moves up or down a file.
    """
    if not location:
        return ""
    segments = location.split(":")
    while len(segments) > 1 and segments[-1].isdigit():
        segments = segments[:-1]
    return ":".join(segments)


def fingerprint(check: str, finding: dict[str, Any]) -> str:
    """Stable, line/column-drift-tolerant fingerprint of a finding."""
    raw = "\x00".join(
        [
            check,
            str(finding.get("kind", "")),
            _path_of(finding.get("location")),
            str(finding.get("message", "")),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load(path: Path) -> dict[str, set[str]]:
    """Load a baseline file into ``{check: {fingerprints}}``; empty if missing."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    checks = data.get("checks", {}) if isinstance(data, dict) else {}
    out: dict[str, set[str]] = {}
    if isinstance(checks, dict):
        for name, fps in checks.items():
            if isinstance(fps, list):
                out[str(name)] = {str(fp) for fp in fps}
    return out


def save(path: Path, baseline: dict[str, set[str]]) -> None:
    """Write a baseline file deterministically (sorted checks + fingerprints)."""
    doc = {
        "schema": BASELINE_SCHEMA,
        "checks": {name: sorted(fps) for name, fps in sorted(baseline.items())},
    }
    path.write_text(
        json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def fingerprints_for(check: str, findings: list[dict[str, Any]]) -> set[str]:
    """Fingerprints for a list of finding dicts (``Finding.to_dict()`` shape)."""
    return {fingerprint(check, finding) for finding in findings}


def gate(execution_status: str, parser_ok: bool, new_count: int) -> dict[str, Any]:
    """Derive the gate decision (see module docstring for the trust rules)."""
    if not parser_ok or execution_status in ("error", "parse_mismatch"):
        return {
            "status": "unavailable",
            "policy": "no_new_findings",
            "reason": (
                f"execution '{execution_status}' — evidence not trustworthy for "
                "baseline"
            ),
        }
    if new_count == 0:
        return {"status": "pass", "policy": "no_new_findings"}
    return {
        "status": "fail",
        "policy": "no_new_findings",
        "reason": f"{new_count} new finding(s) not in baseline",
    }


def gate_exit(gate_status: str | None, execution_exit: int) -> int:
    """Process exit for ``--gate``: gate pass→0, fail→1, else execution exit."""
    if gate_status == "pass":
        return 0
    if gate_status == "fail":
        return 1
    return execution_exit  # unavailable / absent -> honest execution exit


def combine_gate(members: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Aggregate gate over member digests: unavailable > fail > pass.

    Returns ``None`` when no member carried a gate (baseline not configured).
    """
    statuses = [m["gate"]["status"] for m in members if isinstance(m.get("gate"), dict)]
    if not statuses:
        return None
    if "unavailable" in statuses:
        return {"status": "unavailable", "policy": "no_new_findings"}
    if "fail" in statuses:
        return {"status": "fail", "policy": "no_new_findings"}
    return {"status": "pass", "policy": "no_new_findings"}
