# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""ckdn: deterministic check runner and log digester for AI-assisted development."""

__version__ = "1.1.1"

#: Digest document schema identifier. Bump the trailing integer on any
#: backward-incompatible change to the digest.json structure.
DIGEST_SCHEMA = "ckdn.digest/2"

#: Meta document schema identifier.
META_SCHEMA = "ckdn.meta/1"

#: Alias aggregate document schema identifier. Bump the trailing integer on any
#: backward-incompatible change to the aggregate structure.
AGGREGATE_SCHEMA = "ckdn.aggregate/1"
