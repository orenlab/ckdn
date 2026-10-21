# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""pip-audit parser backed by the JSON report written to a file.

Expected command shape:

    uv run pip-audit --progress-spinner off -f json -o {run_dir}/pip-audit.json

Network tool: set ``timeout`` on the check; offline runs land in ``error``,
which is correct.

A package pip-audit could not audit (not on PyPI -- typically the project
itself -- or skipped as editable) is unverified, not clean, and fails the
gate. Option ``allow_skipped``: ``true``, or a list of the package names
whose absence from the audit is accepted.
"""

from __future__ import annotations

import re
from typing import Any

from ckdn.parsers.base import (
    Finding,
    ParseContext,
    ParseResult,
    clamp,
    load_json_artifact,
)


def _vuln_finding(
    name: str,
    version: str,
    vuln: dict[str, Any],
    max_snippet_lines: int,
) -> Finding:
    vuln_id = str(vuln.get("id") or "?")
    description = str(vuln.get("description") or "")[:400]
    detail_lines: list[str] = []
    fix_versions = vuln.get("fix_versions")
    if isinstance(fix_versions, list) and fix_versions:
        detail_lines.append("fix_versions: " + ", ".join(str(v) for v in fix_versions))
    aliases = vuln.get("aliases")
    if isinstance(aliases, list) and aliases:
        detail_lines.append("aliases: " + ", ".join(str(a) for a in aliases))
    return Finding(
        id=f"{name}=={version} {vuln_id}",
        kind="vulnerability",
        message=description,
        location=None,
        detail=tuple(clamp(detail_lines, max_snippet_lines)),
    )


def _collect_findings(
    dependencies: list[Any],
    max_snippet_lines: int,
) -> tuple[list[Finding], set[str]]:
    findings: list[Finding] = []
    packages_with_vulns: set[str] = set()
    for dep in (d for d in dependencies if isinstance(d, dict)):
        name = str(dep.get("name") or "?")
        version = str(dep.get("version") or "?")
        vulns = dep.get("vulns")
        if not isinstance(vulns, list):
            continue
        pkg = f"{name}=={version}"
        for vuln in (v for v in vulns if isinstance(v, dict)):
            packages_with_vulns.add(pkg)
            findings.append(_vuln_finding(name, version, vuln, max_snippet_lines))
    return findings, packages_with_vulns


def _canonical_name(name: str) -> str:
    """PEP 503 normalization, the spelling pip-audit itself reports."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _skipped(root: dict[str, Any], dependencies: list[Any]) -> list[tuple[str, str]]:
    """``(name, reason)`` for every package pip-audit could not audit.

    pip-audit lists them inside ``dependencies``, with a ``skip_reason`` and
    no ``vulns``. A top-level ``skipped`` array is read too: it is the shape
    this parser used to look for, and real reports never carry it -- which
    is how unaudited packages went unmentioned.
    """
    out = [
        (str(dep.get("name") or "?"), str(dep.get("skip_reason") or ""))
        for dep in dependencies
        if isinstance(dep, dict) and "skip_reason" in dep
    ]
    legacy = root.get("skipped")
    for item in legacy if isinstance(legacy, list) else []:
        if isinstance(item, dict):
            out.append(
                (str(item.get("name") or "?"), str(item.get("skip_reason") or ""))
            )
        else:
            out.append((str(item), ""))
    return out


def _apply_skip_gate(
    ctx: ParseContext, result: ParseResult, skipped: list[tuple[str, str]]
) -> None:
    if not skipped:
        return
    allow = ctx.options.get("allow_skipped", False)
    if isinstance(allow, bool):
        accepted = {name for name, _ in skipped} if allow else set()
    elif isinstance(allow, list) and all(isinstance(n, str) for n in allow):
        wanted = {_canonical_name(n) for n in allow}
        accepted = {name for name, _ in skipped if _canonical_name(name) in wanted}
    else:
        result.parser_ok = False
        result.notes.append("`allow_skipped` must be true or a list of package names")
        return
    refused = [(name, why) for name, why in skipped if name not in accepted]
    if accepted:
        result.notes.append(
            f"{len(skipped) - len(refused)} package(s) not audited, accepted by "
            "allow_skipped: " + ", ".join(sorted(accepted))
        )
    if not refused:
        return
    shown = refused[: max(ctx.top, 1)]
    result.gate_failures.append(
        f"pip-audit could not audit {len(refused)} package(s): "
        + ", ".join(name for name, _ in shown)
        + (" …" if len(refused) > len(shown) else "")
        + "; unaudited is not clean (accept known ones with `allow_skipped`)"
    )
    result.notes.extend(f"{name}: {why}" for name, why in shown if why)


class PipAuditJsonParser:
    name = "pip_audit"

    def parse(self, ctx: ParseContext) -> ParseResult:
        root, err = load_json_artifact(
            ctx,
            default_name="pip-audit.json",
            tool="pip-audit",
            expect=dict,
            missing_hint=(
                "The check command must include `-f json -o {run_dir}/pip-audit.json`."
            ),
        )
        if err is not None:
            return err
        dependencies = root.get("dependencies")
        if not isinstance(dependencies, list):
            return ParseResult(
                parser_ok=False,
                notes=[
                    "pip-audit report missing top-level `dependencies` array; "
                    "unexpected format"
                ],
            )

        findings, packages_with_vulns = _collect_findings(
            dependencies, ctx.max_snippet_lines
        )
        skipped = _skipped(root, dependencies)
        result = ParseResult(
            findings=findings,
            summary={
                "vulnerable_packages": len(packages_with_vulns),
                "total_vulnerabilities": len(findings),
                "skipped_packages": len(skipped),
            },
        )
        _apply_skip_gate(ctx, result, skipped)
        return result
