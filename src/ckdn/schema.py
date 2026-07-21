# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Access to the packaged ckdn JSON Schemas.

The schema *documents* live under :mod:`ckdn.schemas` as data files and ship
in the wheel. This module maps each ckdn schema identifier (the ``schema``
field ckdn writes into every document) to its packaged file and loads it.

Loading is stdlib-only (``importlib.resources`` + ``json``); the core CLI
keeps its zero-dependency guarantee. Validating a document against a schema
is a downstream / test concern and may use a real validator such as
``jsonschema``.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, cast

from ckdn import AGGREGATE_SCHEMA, DIGEST_SCHEMA, META_SCHEMA

#: ckdn schema identifier -> packaged schema filename.
SCHEMA_FILES: dict[str, str] = {
    DIGEST_SCHEMA: "ckdn.digest.v2.schema.json",
    AGGREGATE_SCHEMA: "ckdn.aggregate.v1.schema.json",
    META_SCHEMA: "ckdn.meta.v1.schema.json",
}


def schema_ids() -> list[str]:
    """Return the ckdn schema identifiers that have a packaged schema."""
    return sorted(SCHEMA_FILES)


def load_schema(schema_id: str) -> dict[str, Any]:
    """Load a packaged JSON Schema by its ckdn schema identifier.

    ``schema_id`` is the value ckdn writes into a document's ``schema`` field
    (e.g. ``"ckdn.digest/2"``). Raises :class:`ValueError` for an unknown id.
    """
    try:
        filename = SCHEMA_FILES[schema_id]
    except KeyError:
        known = ", ".join(schema_ids())
        raise ValueError(
            f"no packaged schema for {schema_id!r}; known: {known}"
        ) from None
    text = (files("ckdn.schemas") / filename).read_text(encoding="utf-8")
    return cast("dict[str, Any]", json.loads(text))
