# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Shared test helpers for worktree / temp-config layouts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorktreeSlice:
    """Config file outside the project tree; cwd targets the worktree root."""

    config_dir: Path
    worktree: Path
    cfg_path: Path


def make_worktree_slice(tmp_path: Path, *, body: str) -> WorktreeSlice:
    """Create config_dir/ckdn.toml + separate worktree directory."""
    config_dir = tmp_path / "cfg"
    worktree = tmp_path / "wt"
    config_dir.mkdir()
    worktree.mkdir()
    cfg_path = config_dir / "ckdn.toml"
    cfg_path.write_text(body, encoding="utf-8")
    return WorktreeSlice(config_dir=config_dir, worktree=worktree, cfg_path=cfg_path)
