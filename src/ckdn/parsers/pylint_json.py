# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""pylint parser backed by the json2 report (pylint >= 3.0).

Expected command shape:

    uv run pylint src --output-format=json2:{run_dir}/pylint.json

pylint's rc is a bitmask over the message classes that carry weight —
F/E/W/R/C map to bits 1/2/4/8/16 — so any of those makes it nonzero and each
becomes a finding. The informational class (``I``: ``useless-suppression``,
``locally-disabled``) owns no bit: a run whose only messages are
informational exits 0. Turning those into findings would pit findings against
``rc == 0``, which reconciles to ``parse_mismatch`` — a permanently untrusted
check nobody can fix. So informational messages are counted in
``summary.info_count`` and never emitted as findings, the same contract ``ty``,
``mypy`` and ``pyright`` use for warnings. They are still tallied in
``by_type``, because ``statistics.messageTypeCount`` declares an ``info``
bucket that the cross-check has to keep matching.

Optional ``score_fail_under`` gate mirrors coverage's ``fail_under``.
"""

from __future__ import annotations

from typing import Any

from ckdn.parsers.base import (
    Finding,
    ParseContext,
    ParseResult,
    format_location,
    load_json_artifact,
    top_counts,
)

#: pylint's json2 spelling of the informational class. It is the one class
#: with no bit in the exit-code bitmask, so it is evidence, not failure.
INFORMATIONAL_TYPE = "info"


def _message_finding(msg: dict[str, Any]) -> Finding:
    message_id = str(msg.get("messageId") or msg.get("message-id") or "?")
    path = str(msg.get("path") or msg.get("module") or "?")
    location = format_location(path, msg.get("line"), msg.get("column"))
    return Finding(
        id=f"{message_id} {location}",
        kind="lint_violation",
        message=str(msg.get("message") or "")[:400],
        location=location,
    )


class PylintJsonParser:
    name = "pylint"

    def parse(self, ctx: ParseContext) -> ParseResult:
        match load_json_artifact(
            ctx,
            default_name="pylint.json",
            tool="pylint",
            expect=dict,
            missing_hint=(
                "The check command must include "
                "`--output-format=json2:{run_dir}/pylint.json` "
                "(requires pylint >= 3.0)."
            ),
            nested_key="messages",
            nested_expect=list,
            nested_missing_note=(
                "pylint report missing `messages` array; unexpected format"
            ),
        ):
            case (_, err) if err is not None:
                return err
            case ((data, messages), None):
                pass
            # Kept so the match stays exhaustive: the loader returns a payload
            # or an error, never both and never neither.
            case _:  # pragma: no cover - the loader always returns one of the two
                return ParseResult(
                    parser_ok=False,
                    notes=["pylint report could not be loaded"],
                )

        findings: list[Finding] = []
        by_type: dict[str, int] = {}
        by_symbol: dict[str, int] = {}
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            msg_type = str(msg.get("type") or "?").lower()
            symbol = str(
                msg.get("symbol")
                or msg.get("messageId")
                or msg.get("message-id")
                or "?"
            )
            by_type[msg_type] = by_type.get(msg_type, 0) + 1
            by_symbol[symbol] = by_symbol.get(symbol, 0) + 1
            # Informational messages are census, not failure evidence: they
            # cannot lift pylint's rc off 0, so a finding here would read as
            # `parse_mismatch`. They stay in `by_type` for the cross-check.
            if msg_type == INFORMATIONAL_TYPE:
                continue
            findings.append(_message_finding(msg))

        raw_stats = data.get("statistics")
        statistics = raw_stats if isinstance(raw_stats, dict) else {}
        score = statistics.get("score")
        result = ParseResult(
            findings=findings,
            summary={
                "message_count": len(findings),
                "info_count": by_type.get(INFORMATIONAL_TYPE, 0),
                "by_type": top_counts(by_type, ctx.top),
                "by_symbol": top_counts(by_symbol, ctx.top),
                "score": score,
            },
        )
        self._verify_counts(result, by_type, statistics)
        self._apply_score_gate(ctx, result, score)
        return result

    @staticmethod
    def _verify_counts(
        result: ParseResult,
        by_type: dict[str, int],
        statistics: dict[str, Any],
    ) -> None:
        declared = statistics.get("messageTypeCount")
        if not isinstance(declared, dict):
            return
        for key, raw in declared.items():
            key_s = str(key).lower()
            expected = int(raw) if isinstance(raw, (int, float)) else None
            if expected is None:
                continue
            actual = by_type.get(key_s, 0)
            if actual != expected:
                result.parser_ok = False
                result.notes.append(
                    f"pylint statistics.messageTypeCount[{key}]={expected} "
                    f"but {actual} were parsed; refusing to trust this parse"
                )
                return

    @staticmethod
    def _apply_score_gate(
        ctx: ParseContext,
        result: ParseResult,
        score: Any,
    ) -> None:
        threshold = ctx.options.get("score_fail_under")
        skip_note = None
        if threshold is None:
            skip_note = (
                "no `score_fail_under` configured for this check; "
                "pylint score gate skipped"
            )
        elif score is None:
            skip_note = "pylint report has no statistics.score; score gate skipped"
        if skip_note is not None:
            result.notes.append(skip_note)
            return
        assert threshold is not None and score is not None
        try:
            score_f = float(score)
            under = float(threshold)
        except (TypeError, ValueError):
            result.parser_ok = False
            result.notes.append("pylint score or score_fail_under is not numeric")
            return
        if score_f < under:
            result.gate_failures.append(
                f"pylint score {score_f} is below score_fail_under={under}"
            )
