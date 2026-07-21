# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Third-party parser discovery via the ``ckdn.parsers`` entry-point group."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import ckdn.parsers as parsers
from ckdn.parsers.base import ParseContext, ParseResult


class _PluginParser:
    name = "myplugin"

    def parse(self, ctx: ParseContext) -> ParseResult:
        return ParseResult(parser_ok=True, summary={"plugin": True})


class _FakeEntryPoint:
    """Minimal stand-in for importlib.metadata.EntryPoint."""

    def __init__(self, obj: object) -> None:
        self._obj = obj

    def load(self) -> object:
        return self._obj


@pytest.fixture
def clear_plugin_cache() -> Iterator[None]:
    parsers._plugins.cache_clear()
    yield
    parsers._plugins.cache_clear()


def _patch_entry_points(
    monkeypatch: pytest.MonkeyPatch, eps: list[_FakeEntryPoint]
) -> None:
    def fake_entry_points(*, group: str) -> list[_FakeEntryPoint]:
        assert group == parsers.PLUGIN_GROUP
        return eps

    monkeypatch.setattr(parsers, "entry_points", fake_entry_points)


def test_plugin_class_is_discovered(
    monkeypatch: pytest.MonkeyPatch, clear_plugin_cache: None
) -> None:
    _patch_entry_points(monkeypatch, [_FakeEntryPoint(_PluginParser)])
    parser = parsers.get_parser("myplugin")
    assert parser is not None
    assert parser.name == "myplugin"
    assert "myplugin" in parsers.available_parsers()


def test_plugin_instance_is_accepted(
    monkeypatch: pytest.MonkeyPatch, clear_plugin_cache: None
) -> None:
    _patch_entry_points(monkeypatch, [_FakeEntryPoint(_PluginParser())])
    parser = parsers.get_parser("myplugin")
    assert parser is not None and parser.name == "myplugin"


def test_builtin_is_never_shadowed(
    monkeypatch: pytest.MonkeyPatch, clear_plugin_cache: None
) -> None:
    class Impostor:
        name = "pytest"

        def parse(self, ctx: ParseContext) -> ParseResult:
            return ParseResult(parser_ok=False, notes=["impostor"])

    _patch_entry_points(monkeypatch, [_FakeEntryPoint(Impostor)])
    parser = parsers.get_parser("pytest")
    assert parser is not None
    assert type(parser).__name__ == "PytestJUnitParser"


def test_broken_plugin_is_skipped_not_raised(
    monkeypatch: pytest.MonkeyPatch, clear_plugin_cache: None
) -> None:
    class Boom:
        name = "boom"

        def __init__(self) -> None:
            raise RuntimeError("plugin import blew up")

    _patch_entry_points(monkeypatch, [_FakeEntryPoint(Boom)])
    # discovery must not raise, and the broken parser is simply unavailable
    assert parsers.get_parser("boom") is None
    assert "boom" not in parsers.available_parsers()


def test_no_plugins_leaves_builtins_intact(
    monkeypatch: pytest.MonkeyPatch, clear_plugin_cache: None
) -> None:
    _patch_entry_points(monkeypatch, [])
    assert parsers.get_parser("ruff") is not None
    assert parsers.get_parser("nonexistent") is None
