from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Annotated

import msgspec
import pytest

from sprite_compile.config import ConfigSection, Setting, check_settings, load_config
from sprite_compile.config_sync import check_config, run_config_command, sync_config, synced_text


class Limits(ConfigSection):
    burst: Annotated[int, Setting("Requests allowed at once.", added="0.6.0")] = 3


class Playlists(ConfigSection):
    max_items: Annotated[int, Setting("Maximum entries from one playlist link.", added="0.4.0")] = 100
    sources: Annotated[list[str], Setting("Sites to accept links from.", added="0.5.0")] = msgspec.field(
        default_factory=lambda: ["youtube", "bandcamp"]
    )
    limits: Annotated[Limits, Setting("Rate limits for playlist imports.", added="0.6.0")] = msgspec.field(
        default_factory=Limits
    )


class Settings(ConfigSection, kw_only=True):
    guild_id: Annotated[int, Setting("Guild to register commands in.", added="0.1.0")]
    timeout: Annotated[timedelta, Setting("Download timeout.", added="0.2.0")] = timedelta(seconds=30)
    nickname: Annotated[str | None, Setting("Nickname in the guild.", added="0.3.0")] = None
    playlists: Annotated[Playlists, Setting("Playlist imports.", added="0.4.0")] = msgspec.field(
        default_factory=Playlists
    )


class Undocumented(ConfigSection):
    name: Annotated[str, Setting("A name.", added="0.1.0")] = ""
    inner: Annotated[Limits, Setting("Limits.", added="0.1.0")] = msgspec.field(default_factory=Limits)
    color: str = "blue"


def synced(text: str) -> str:
    return synced_text(Settings, text, "0.7.0")[0]


def uncommented(text: str) -> str:
    return "\n".join(line.removeprefix("# ") for line in text.splitlines() if " (added in " not in line)
def uncommented(text: str) -> str:
    """The synced text with every added key that has a default uncommented."""
    lines = [line.removeprefix("# ") for line in text.splitlines() if " (added in " not in line]
    return "\n".join(line for line in lines if not line.endswith(" ="))
def test_added_keys_are_commented_out_with_description_and_default():
    text = synced("guild_id = 7\n")

    assert "# Download timeout. (added in 0.2.0)\n# timeout = \"PT30S\"\n" in text
    assert "# Nickname in the guild. (added in 0.3.0; unset by default)\n# nickname =\n" in text
    assert msgspec.toml.decode(text, type=Settings) == Settings(guild_id=7)


def test_uncommenting_added_keys_gives_the_defaults():
    text = synced('guild_id = 7\nnickname = "chance"\n')

    assert msgspec.toml.decode(uncommented(text), type=Settings) == Settings(guild_id=7, nickname="chance")


def test_top_level_keys_are_added_before_the_first_table():
    text = synced("guild_id = 7\n\n[playlists]\nmax_items = 5\n")

    assert text.index("# timeout =") < text.index("[playlists]")
    assert text.index("[playlists]") < text.index("# sources =")
    assert msgspec.toml.decode(text, type=Settings).playlists.max_items == 5


def test_removed_keys_and_tables_are_commented_out_where_they_were():
    text = synced('guild_id = 7  # test guild\nold = "x"\n\n[playlists]\ngone = [1,\n  2]\n\n[obsolete]\na = 1\n')

    assert 'guild_id = 7  # test guild\n# removed in 0.7.0:\n# old = "x"\n' in text
    assert "# removed in 0.7.0:\n# gone = [1,\n#   2]\n" in text
    assert "# removed in 0.7.0:\n# [obsolete]\n# a = 1\n" in text
    assert msgspec.toml.decode(text, type=Settings) == Settings(guild_id=7)


def test_sync_is_idempotent():
    text = synced("guild_id = 7\nold = 1\n\n[playlists]\nmax_items = 5\n")

    again, report = synced_text(Settings, text, "0.8.0")
    assert again == text
    assert report.in_sync

def test_keys_commented_out_by_hand_count_as_present():
    text = 'guild_id = 7\n# timeout = "PT1M"\n\n# [playlists]\n# max_items = 5\n'

    synced_text_, report = synced_text(Settings, text, "0.7.0")
    assert report.added == ["nickname", "playlists.sources", "playlists.limits"]
    assert "# [playlists]\n# max_items = 5\n# Sites to accept links from. (added in 0.5.0)\n" in synced_text_


def test_keys_added_to_a_commented_out_table_go_into_it():
    text = synced("guild_id = 7\n")
    newer = text.replace("# Sites to accept links from. (added in 0.5.0)\n", "").replace(
        '# sources = ["youtube", "bandcamp"]\n', ""
    )

    assert synced(newer) == text


def test_table_without_its_own_header_gets_a_commented_header():
    text = synced("guild_id = 7\n\n[playlists.limits]\nburst = 1\n")

    assert "# [playlists]\n# Maximum entries from one playlist link. (added in 0.4.0)\n# max_items = 100\n" in text
    assert msgspec.toml.decode(uncommented(text), type=Settings).playlists == Playlists(limits=Limits(burst=1))


def test_table_split_in_parts_gets_each_missing_key_once():
    text = synced("guild_id = 7\n\n[playlists]\nmax_items = 5\n\n[obsolete]\n\n[playlists.limits]\nburst = 1\n")

    assert text.count("# sources =") == 1
    assert msgspec.toml.decode(text, type=Settings).playlists == Playlists(max_items=5, limits=Limits(burst=1))


def test_sync_writes_the_file(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text("guild_id = 7\n", encoding="utf-8")

    assert run_config_command("sync", Settings, path, "0.7.0") == 0
    assert "# timeout =" in path.read_text(encoding="utf-8")
    assert run_config_command("check", Settings, path, "0.7.0") == 0


def test_check_fails_when_out_of_sync_without_writing(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text("guild_id = 7\nold = 1\n", encoding="utf-8")

    problems = check_config(Settings, path, "0.7.0")

    assert any("missing timeout" in p for p in problems)
    assert any("old is no longer a setting" in p for p in problems)
    assert path.read_text(encoding="utf-8") == "guild_id = 7\nold = 1\n"
    assert run_config_command("check", Settings, path, "0.7.0") == 1


def test_check_reports_required_keys_left_commented_out(tmp_path: Path):
    path = tmp_path / "config.toml"
    sync_config(Settings, path, "0.7.0")

    assert "# guild_id =" in path.read_text(encoding="utf-8")
    assert any("`guild_id`" in p for p in check_config(Settings, path, "0.7.0"))
    assert load_config(Playlists, path) == Playlists()


def test_check_settings_names_every_undocumented_field():
    with pytest.raises(TypeError) as error:
        check_settings(Undocumented)
    assert "color" in str(error.value)
    assert "inner.burst" not in str(error.value)
    check_settings(Settings)
