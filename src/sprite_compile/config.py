"""Config: msgspec structs with defaults in code, loaded from the instance's `config.toml`.

A bot declares its config as `ConfigSection` subclasses; a field whose type is another section is a TOML table. The
file only holds what differs from the defaults. Unknown keys, missing required keys and wrong types fail loading with
an error naming the key and the file.

Every field carries a `Setting` with its description and the bot version that added it, which `config sync` writes
next to the key:

    max_playlist_items: Annotated[int, Setting("Maximum entries taken from one playlist link.", added="0.4.0")] = 100

`check_settings()` fails on fields without one; a bot's test suite should run it on its config model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, get_args, get_origin

import msgspec

log = logging.getLogger(__name__)


class ConfigSection(msgspec.Struct, forbid_unknown_fields=True):
    """Base for config structs; subclasses inherit the rejection of undeclared keys. (`kw_only=True` is not
    inherited: a section that lists required fields after defaulted ones has to pass it itself.)"""


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Setting:
    description: str
    added: str


@dataclass(frozen=True)
class ConfigField:
    key: str
    setting: Setting | None
    section: type[ConfigSection] | None  # set when the field is a TOML table
    required: bool
    default: Any  # None when required


def config_fields(section_type: type[ConfigSection]) -> list[ConfigField]:
    result = []
    for info in msgspec.structs.fields(section_type):
        hint, metadata = info.type, ()
        if get_origin(hint) is Annotated:
            hint, *metadata = get_args(hint)
        setting = next((m for m in metadata if isinstance(m, Setting)), None)
        section = hint if isinstance(hint, type) and issubclass(hint, ConfigSection) else None
        required = info.required
        default = None if required else info.default_factory() if info.default is msgspec.NODEFAULT else info.default
        result.append(ConfigField(info.encode_name, setting, section, required, default))
    return result


def check_settings(config_type: type[ConfigSection]) -> None:
    """Raise listing every config field, in every section, that has no `Setting`."""
    missing = []

    def visit(section_type: type[ConfigSection], prefix: str) -> None:
        for field in config_fields(section_type):
            if field.setting is None:
                missing.append(prefix + field.key)
            if field.section is not None:
                visit(field.section, f"{prefix}{field.key}.")

    visit(config_type, "")
    if missing:
        raise TypeError("These config fields have no Setting(description, added=...):\n  " + "\n  ".join(missing))


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
