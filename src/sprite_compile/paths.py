"""Where an instance's files are: set by the base image's environment when deployed, platformdirs in development."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import PlatformDirs

DATA_DIR_ENV = "SPRITE_DATA_DIR"
CONFIG_FILE_ENV = "SPRITE_CONFIG"


def env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def platform_dirs(app_name: str) -> PlatformDirs:
    return PlatformDirs(appname=app_name, appauthor=False)


def state_dir(app_name: str) -> Path:
    """`$SPRITE_DATA_DIR` in a deployed instance, else the user state dir."""
    return env_path(DATA_DIR_ENV) or Path(platform_dirs(app_name).user_state_dir)


def cache_dir(app_name: str) -> Path:
    """`$SPRITE_DATA_DIR` in a deployed instance, else the user cache dir."""
    return env_path(DATA_DIR_ENV) or Path(platform_dirs(app_name).user_cache_dir)


def config_path(app_name: str) -> Path:
    """`$SPRITE_CONFIG` in a deployed instance, else `config.toml` in the user config dir."""
    return env_path(CONFIG_FILE_ENV) or Path(platform_dirs(app_name).user_config_dir) / "config.toml"
