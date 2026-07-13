# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Per-request / server config resolution for MCP tools."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ckdn.app.errors import ConfigLoadError
from ckdn.config import CONFIG_NAME, Config, ConfigError, load_config


@dataclass(frozen=True)
class ServerContext:
    """Default config path and cwd for the MCP process (overridable per tool call)."""

    default_config: Path | None = None
    default_cwd: Path | None = None

    def resolve_config_path(self, config: str | None = None) -> Path | None:
        if config:
            return Path(config)
        if self.default_config is not None:
            return self.default_config
        env = os.environ.get("CKDN_CONFIG")
        if env:
            return Path(env)
        return None

    def resolve_cwd(self, cwd: str | None = None) -> Path | None:
        if cwd:
            return Path(cwd).resolve()
        if self.default_cwd is not None:
            return self.default_cwd
        env = os.environ.get("CKDN_CWD")
        if env:
            return Path(env).resolve()
        return None

    def load(self, config: str | None = None, cwd: str | None = None) -> Config:
        path = self.resolve_config_path(config)
        resolved_cwd = self.resolve_cwd(cwd)
        try:
            return load_config(path, cwd=resolved_cwd)
        except ConfigError as exc:
            hint = path if path is not None else Path.cwd() / CONFIG_NAME
            raise ConfigLoadError(f"{exc} (config={hint})") from exc
