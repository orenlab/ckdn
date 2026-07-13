# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Built-in parser registry."""

from __future__ import annotations

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


def get_parser(name: str) -> Parser | None:
    return _REGISTRY.get(name)


def available_parsers() -> list[str]:
    return sorted(_REGISTRY)
