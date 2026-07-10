# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""pip-audit parser backed by the JSON report written to a file.

Expected command shape:

    uv run pip-audit --progress-spinner off -f json -o {run_dir}/pip-audit.json

Network tool: set ``timeout`` on the check; offline runs land in ``error``,
which is correct.
"""

from __future__ import annotations

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
        detail_lines.append(
            "fix_versions: " + ", ".join(str(v) for v in fix_versions)
        )
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
        skipped = root.get("skipped") or []
        skipped_count = len(skipped) if isinstance(skipped, list) else 0
        result = ParseResult(
            findings=findings,
            summary={
                "vulnerable_packages": len(packages_with_vulns),
                "total_vulnerabilities": len(findings),
                "skipped_packages": skipped_count,
            },
        )
        if skipped_count > 0:
            result.notes.append(
                f"{skipped_count} package(s) were skipped by pip-audit "
                "(unverified, not clean)"
            )
        return result
