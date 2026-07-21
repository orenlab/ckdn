# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Built-in parser registry plus third-party plugin discovery.

Built-ins are defined in ``_REGISTRY``. Third-party parsers register under the
``ckdn.parsers`` entry-point group and are discovered lazily. Built-in names
are authoritative and are never shadowed by a plugin; a plugin that fails to
import (or collides with a built-in) is skipped, so a broken third-party
package can never break ckdn's own checks. An unresolved reference surfaces at
the point of use as :class:`~ckdn.app.errors.UnknownParserError`, not as a
silent wrong result.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.metadata import entry_points

from ckdn.parsers.bandit_json import BanditJsonParser
from ckdn.parsers.base import Parser
from ckdn.parsers.coverage_xml import CoverageXmlParser
from ckdn.parsers.generic import GenericParser
from ckdn.parsers.mypy import MypyParser
from ckdn.parsers.pip_audit_json import PipAuditJsonParser
from ckdn.parsers.pre_commit_text import PreCommitTextParser
from ckdn.parsers.pylint_json import PylintJsonParser
from ckdn.parsers.pyright_json import PyrightJsonParser
from ckdn.parsers.pytest_junit import PytestJUnitParser
from ckdn.parsers.reformat_text import ReformatTextParser
from ckdn.parsers.ruff_json import RuffJsonParser
from ckdn.parsers.sarif import SarifParser
from ckdn.parsers.ty_text import TyTextParser

#: Entry-point group third-party parser packages register under.
PLUGIN_GROUP = "ckdn.parsers"

#: Parsers are stateless, so a single shared instance per kind is enough.
_REGISTRY: dict[str, Parser] = {
    parser.name: parser
    for parser in (
        GenericParser(),
        PytestJUnitParser(),
        CoverageXmlParser(),
        TyTextParser(),
        RuffJsonParser(),
        MypyParser(),
        PyrightJsonParser(),
        ReformatTextParser(),
        PipAuditJsonParser(),
        PreCommitTextParser(),
        BanditJsonParser(),
        PylintJsonParser(),
        SarifParser(),
    )
}


@lru_cache(maxsize=1)
def _plugins() -> dict[str, Parser]:
    """Discover third-party parsers under the ``ckdn.parsers`` group.

    Cached for the process. Built-ins win on a name collision; an entry point
    that fails to load is skipped (never raised) so an unrelated broken plugin
    cannot brick ckdn. The entry-point value may resolve to a ``Parser`` class
    (instantiated with no arguments) or an already-built instance.
    """
    found: dict[str, Parser] = {}
    for ep in entry_points(group=PLUGIN_GROUP):
        try:
            obj = ep.load()
            parser = obj() if isinstance(obj, type) else obj
            name = parser.name
        except Exception:
            # A broken third-party plugin must not brick ckdn; skip it.
            continue
        if name in _REGISTRY or name in found:
            continue  # never shadow a built-in or an earlier plugin
        found[name] = parser
    return found


def get_parser(name: str) -> Parser | None:
    """Return the parser registered under ``name`` (built-in or plugin)."""
    builtin = _REGISTRY.get(name)
    if builtin is not None:
        return builtin
    return _plugins().get(name)


def available_parsers() -> list[str]:
    """Names of all resolvable parsers, built-in and plugin, sorted."""
    return sorted(_REGISTRY.keys() | _plugins().keys())
