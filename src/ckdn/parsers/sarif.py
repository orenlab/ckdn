# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""SARIF format parser — tool-agnostic (semgrep, gitleaks, trivy, CodeQL, …).

Expected command shape (example):

    uvx semgrep scan --config auto --sarif-output {run_dir}/report.sarif .

Findings are results whose ``level`` is in option ``fail_levels``
(default ``["error"]``). Everything else is counted in the summary only.
If a tool exits nonzero on warnings, set
``fail_levels = ["error", "warning"]`` for that check — wrong fail_levels
shows up as parse_mismatch, which is the guard working.
"""

from __future__ import annotations

from typing import Any

from ckdn.parsers.base import (
    Finding,
    ParseContext,
    ParseResult,
    load_json_artifact,
    top_counts,
)


def _result_level(item: dict[str, Any]) -> str:
    """SARIF: missing level defaults to warning."""
    raw = item.get("level")
    return "warning" if raw is None else str(raw).lower()


def _first_location(item: dict[str, Any]) -> str | None:
    locations = item.get("locations")
    if not isinstance(locations, list) or not locations:
        return None
    loc0 = locations[0]
    if not isinstance(loc0, dict):
        return None
    phys = loc0.get("physicalLocation") or {}
    if not isinstance(phys, dict):
        return None
    artifact = phys.get("artifactLocation") or {}
    uri = str(artifact.get("uri") or "") if isinstance(artifact, dict) else ""
    region = phys.get("region") or {}
    start_line = region.get("startLine") if isinstance(region, dict) else None
    if uri and start_line is not None:
        return f"{uri}:{start_line}"
    return uri or None


def _message_text(item: dict[str, Any]) -> str:
    message = item.get("message")
    if isinstance(message, dict):
        return str(message.get("text") or "")[:400]
    return str(message or "")[:400]


def _fail_levels(raw: object) -> set[str]:
    if isinstance(raw, str):
        return {raw.lower()}
    if isinstance(raw, (list, tuple, set)):
        return {str(x).lower() for x in raw}
    return {"error"}


def _driver_meta(run: dict[str, Any]) -> dict[str, str] | None:
    tool = run.get("tool") or {}
    if not isinstance(tool, dict):
        return None
    driver = tool.get("driver") or {}
    if not isinstance(driver, dict):
        return None
    return {
        "name": str(driver.get("name") or ""),
        "version": str(driver.get("version") or ""),
    }


def _ingest_results(
    results: list[Any],
    fail_levels: set[str],
    findings: list[Finding],
    by_level: dict[str, int],
    by_rule: dict[str, int],
) -> None:
    for item in (r for r in results if isinstance(r, dict)):
        level = _result_level(item)
        rule_id = str(item.get("ruleId") or "?")
        by_level[level] = by_level.get(level, 0) + 1
        by_rule[rule_id] = by_rule.get(rule_id, 0) + 1
        if level not in fail_levels:
            continue
        location = _first_location(item)
        findings.append(
            Finding(
                id=f"{rule_id} {location or '?'}",
                kind=f"sarif_{level}",
                message=_message_text(item),
                location=location,
            )
        )


class SarifParser:
    name = "sarif"

    def parse(self, ctx: ParseContext) -> ParseResult:
        data, err = load_json_artifact(
            ctx,
            default_name="report.sarif",
            tool="SARIF",
            expect=dict,
            missing_hint=(
                "Direct the tool's `--sarif` / `--sarif-output` into "
                "`{run_dir}/report.sarif`."
            ),
        )
        if err is not None:
            return err
        if "version" not in data or "runs" not in data:
            return ParseResult(
                parser_ok=False,
                notes=["SARIF report missing `version` or `runs`; unexpected format"],
            )
        runs = data.get("runs")
        if not isinstance(runs, list):
            return ParseResult(
                parser_ok=False,
                notes=["SARIF `runs` is not an array; unexpected format"],
            )

        fail_levels = _fail_levels(ctx.options.get("fail_levels", ["error"]))
        findings: list[Finding] = []
        by_level: dict[str, int] = {}
        by_rule: dict[str, int] = {}
        tools: list[dict[str, str]] = []

        for run in runs:
            if not isinstance(run, dict):
                continue
            meta = _driver_meta(run)
            if meta is not None:
                tools.append(meta)
            results = run.get("results")
            if isinstance(results, list):
                _ingest_results(results, fail_levels, findings, by_level, by_rule)

        return ParseResult(
            findings=findings,
            summary={
                "result_count": sum(by_level.values()),
                "finding_count": len(findings),
                "by_level": top_counts(by_level, ctx.top),
                "by_rule": top_counts(by_rule, ctx.top),
                "tools": tools[: ctx.top],
            },
        )
