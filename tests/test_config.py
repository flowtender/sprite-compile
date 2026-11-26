from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import msgspec
import pytest

from sprite_compile.config import ConfigError, ConfigSection, load_config


class Playlists(ConfigSection):
    max_items: int = 100
    sources: list[str] = msgspec.field(default_factory=lambda: ["youtube"])


class Settings(ConfigSection, kw_only=True):
    playlists: Playlists = msgspec.field(default_factory=Playlists)
    timeout: timedelta = timedelta(seconds=30)
    guild_id: int


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_file_overrides_only_its_keys(tmp_path: Path):
    path = write_config(tmp_path, "guild_id = 7\n[playlists]\nmax_items = 5\n")

    assert load_config(Settings, path) == Settings(guild_id=7, playlists=Playlists(max_items=5))


def test_values_are_converted_to_field_types(tmp_path: Path):
    path = write_config(tmp_path, 'guild_id = 7\ntimeout = "PT90S"\n')

    assert load_config(Settings, path).timeout == timedelta(seconds=90)


@pytest.mark.parametrize(
    ("text", "named"),
    [
        ("guild_id = 7\nguild = 8\n", "`guild`"),
        ("guild_id = 7\n[playlists]\nmax_item = 5\n", "`max_item`"),
        ("[playlists]\nmax_items = 5\n", "`guild_id`"),
        ('guild_id = 7\n[playlists]\nmax_items = "many"\n', "$.playlists.max_items"),
    ],
    ids=["unknown key", "unknown key in table", "missing required key", "wrong type"],
)
def test_invalid_config_names_the_key_and_file(tmp_path: Path, text: str, named: str):
    path = write_config(tmp_path, text)

    with pytest.raises(ConfigError) as error:
        load_config(Settings, path)
    assert named in str(error.value)
    assert str(path) in str(error.value)


def test_toml_syntax_error_names_the_file(tmp_path: Path):
    path = write_config(tmp_path, "guild_id = \n")

    with pytest.raises(ConfigError, match="config.toml"):
        load_config(Settings, path)


def test_missing_file_uses_defaults(tmp_path: Path):
    assert load_config(Playlists, tmp_path / "config.toml") == Playlists()


def test_missing_file_still_reports_required_keys(tmp_path: Path):
    with pytest.raises(ConfigError, match="`guild_id`"):
        load_config(Settings, tmp_path / "config.toml")
