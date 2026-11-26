"""Config: msgspec structs with defaults in code, loaded from the instance's `config.toml`.

A bot declares its config as `ConfigSection` subclasses; a field whose type is another section is a TOML table. The
file only holds what differs from the defaults. Unknown keys, missing required keys and wrong types fail loading with
an error naming the key and the file.
"""

from __future__ import annotations

import logging
from pathlib import Path

import msgspec

log = logging.getLogger(__name__)


class ConfigSection(msgspec.Struct, forbid_unknown_fields=True):
    """Base for config structs; subclasses inherit the rejection of undeclared keys. (`kw_only=True` is not
    inherited: a section that lists required fields after defaulted ones has to pass it itself.)"""


class ConfigError(Exception):
    pass


def load_config[C: ConfigSection](config_type: type[C], path: Path) -> C:
    """The config in `path`; a missing file counts as empty, so only the defaults apply."""
    try:
        text = path.read_bytes()
    except FileNotFoundError:
        log.warning("Config file not found: %s; using defaults", path)
        text = b""
    try:
        return msgspec.toml.decode(text, type=config_type)
    except msgspec.DecodeError as error:  # also covers ValidationError
        raise ConfigError(f"{path}: {error}") from error
