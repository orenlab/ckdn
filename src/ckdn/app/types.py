# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Shared result types for the application layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AtomicRunResult:
    check: str
    status: str
    rc: int
    run_dir: Path
    digest: dict[str, Any]
    exit_code: int
    #: Fingerprints of **every** finding the run produced, not just the ones
    #: the digest's top-N slice shows -- the set ``ckdn baseline`` records.
    #: Empty when ``[run].baseline`` is not configured, since nothing
    #: classifies findings then and there is no baseline to write to.
    fingerprints: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AliasRunResult:
    alias: str
    status: str
    aggregate: dict[str, Any]
    members: tuple[AtomicRunResult, ...]
    exit_code: int
