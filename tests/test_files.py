# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Atomic publication of ckdn's own documents."""

from __future__ import annotations

from pathlib import Path

import pytest

from ckdn._files import write_atomic, write_text_atomic


def test_write_atomic_replaces_the_previous_document_whole(tmp_path: Path) -> None:
    target = tmp_path / "digest.json"
    target.write_text("old and much longer", encoding="utf-8")
    write_text_atomic(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    write_atomic(target, b"raw", durable=False)
    assert target.read_bytes() == b"raw"
    assert [p.name for p in tmp_path.iterdir()] == ["digest.json"]


def test_a_failed_write_leaves_neither_a_torn_document_nor_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "digest.json"
    target.write_text("previous", encoding="utf-8")

    def _boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("ckdn._files.os.replace", _boom)
    with pytest.raises(OSError, match="disk full"):
        write_text_atomic(target, "next")
    assert target.read_text(encoding="utf-8") == "previous"
    assert [p.name for p in tmp_path.iterdir()] == ["digest.json"]
