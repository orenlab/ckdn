# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Stdlib application facade shared by CLI and MCP transports."""

from __future__ import annotations

from ckdn.app.errors import (
    AliasExtraArgsError,
    AppError,
    ArtifactError,
    ConfigLoadError,
    DigestError,
    NotAliasError,
    NotAtomicError,
    RunNotFoundError,
    UnknownCheckError,
    UnknownParserError,
)
from ckdn.app.queries import (
    DEFAULT_EVIDENCE_LIMIT,
    MAX_EVIDENCE_LIMIT,
    get_digest,
    get_evidence,
    list_checks,
    list_runs,
)
from ckdn.app.run import exit_from_outcome, run_alias, run_check, run_one
from ckdn.app.types import AliasRunResult, AtomicRunResult

__all__ = [
    "DEFAULT_EVIDENCE_LIMIT",
    "MAX_EVIDENCE_LIMIT",
    "AliasExtraArgsError",
    "AliasRunResult",
    "AppError",
    "ArtifactError",
    "AtomicRunResult",
    "ConfigLoadError",
    "DigestError",
    "NotAliasError",
    "NotAtomicError",
    "RunNotFoundError",
    "UnknownCheckError",
    "UnknownParserError",
    "exit_from_outcome",
    "get_digest",
    "get_evidence",
    "list_checks",
    "list_runs",
    "run_alias",
    "run_check",
    "run_one",
]
