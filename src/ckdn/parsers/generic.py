# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Generic parser: exit-code-only checks (builds, deploys, scripts).

Produces no findings by construction, so it declares
``evidence_expected=False``: a nonzero exit code reconciles to ``fail``
(with the log tail attached), not to ``error``.
"""

from __future__ import annotations

from ckdn.parsers.base import ParseContext, ParseResult


class GenericParser:
    name = "generic"

    def parse(self, ctx: ParseContext) -> ParseResult:
        lines = ctx.log_text.splitlines()
        return ParseResult(
            parser_ok=True,
            summary={"log_lines": len(lines)},
            evidence_expected=False,
            include_log_tail=ctx.rc != 0,
        )
