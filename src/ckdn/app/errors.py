# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Application-layer errors (transports map these to exit codes / isError)."""

from __future__ import annotations


class AppError(Exception):
    """Base error for ckdn application operations."""


class ConfigLoadError(AppError):
    """ckdn.toml missing or invalid."""


class UnknownCheckError(AppError):
    """Requested check name is not in the config."""


class NotAtomicError(AppError):
    """Operation requires an atomic check."""


class NotAliasError(AppError):
    """Operation requires an alias check."""


class UnknownParserError(AppError):
    """Configured parser name is not registered."""


class AliasExtraArgsError(AppError):
    """Aliases do not accept extra command arguments."""


class RunNotFoundError(AppError):
    """No matching run directory."""


class DigestError(AppError):
    """Digest missing or corrupt."""


class ArtifactError(AppError):
    """Artifact path invalid, missing, or outside the run directory."""
