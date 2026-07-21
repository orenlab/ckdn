# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Packaged JSON Schemas for ckdn output documents.

The ``.json`` files here are the canonical, versioned contract for
``ckdn.digest/2``, ``ckdn.aggregate/1``, and ``ckdn.meta/1``. Load them via
:func:`ckdn.schema.load_schema`. They ship inside the wheel so downstream
consumers can validate ckdn output without vendoring a copy.
"""
