# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Render a stored digest's findings for CI surfaces.

``github`` emits GitHub Actions workflow commands (``::error file=…::message``)
so findings show inline on a pull request. ``sarif`` emits a minimal SARIF
2.1.0 document that can be uploaded to a code-scanning dashboard. Both are pure
projections of an existing ``ckdn.digest/2`` — they never run anything and
never change the run's status.
"""

from __future__ import annotations

from typing import Any


def split_location(location: str | None) -> tuple[str | None, int | None, int | None]:
    """Split a ``path[:line[:col]]`` location into its parts.

    Trailing numeric segments are read as line then column; everything before
    them is the path (which itself never contains a colon in ckdn digests,
    since paths are normalized to forward slashes).
    """
    if not location:
        return None, None, None
    segments = location.split(":")
    if len(segments) >= 3 and segments[-1].isdigit() and segments[-2].isdigit():
        return ":".join(segments[:-2]), int(segments[-2]), int(segments[-1])
    if len(segments) >= 2 and segments[-1].isdigit():
        return ":".join(segments[:-1]), int(segments[-1]), None
    return location, None, None


def _escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    return _escape_data(text).replace(":", "%3A").replace(",", "%2C")


def to_github(digest: dict[str, Any]) -> list[str]:
    """Return one ``::error …`` workflow command per finding."""
    lines: list[str] = []
    for finding in digest.get("findings", []):
        path, line, col = split_location(finding.get("location"))
        params: list[str] = []
        if path:
            params.append(f"file={_escape_property(path)}")
        if line is not None:
            params.append(f"line={line}")
        if col is not None:
            params.append(f"col={col}")
        fid = finding.get("id")
        if fid:
            params.append(f"title={_escape_property(str(fid))}")
        prefix = "::error " + ",".join(params) if params else "::error"
        lines.append(f"{prefix}::{_escape_data(str(finding.get('message', '')))}")
    return lines


def to_sarif(digest: dict[str, Any]) -> dict[str, Any]:
    """Return a minimal SARIF 2.1.0 document for the digest's findings."""
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for finding in digest.get("findings", []):
        kind = str(finding.get("kind", "finding"))
        rules.setdefault(kind, {"id": kind})
        result: dict[str, Any] = {
            "ruleId": kind,
            "level": "error",
            "message": {"text": str(finding.get("message", ""))},
        }
        path, line, col = split_location(finding.get("location"))
        if path:
            region: dict[str, Any] = {}
            if line is not None:
                region["startLine"] = line
            if col is not None:
                region["startColumn"] = col
            physical: dict[str, Any] = {"artifactLocation": {"uri": path}}
            if region:
                physical["region"] = region
            result["locations"] = [{"physicalLocation": physical}]
        results.append(result)
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "ckdn",
                        "informationUri": "https://github.com/orenlab/ckdn",
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
            }
        ],
    }
