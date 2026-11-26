from __future__ import annotations

from pathlib import Path

import pytest

from sprite_compile.codec import MessageCodec
from sprite_compile.discord.avatars import UserAvatarCache
from sprite_compile.storage import DatabaseHandle


@pytest.fixture
def avatars(tmp_path: Path):
    database = DatabaseHandle(tmp_path / "cache.sqlite3", MessageCodec())
    yield UserAvatarCache(database)
    database.close()


def test_avatar_falls_back_to_global_row(avatars: UserAvatarCache):
    avatars.update_avatar(5, 0, "Elle", "https://cdn/global.png")
    avatars.update_avatar(5, 99, "Elle (guild)", "https://cdn/guild.png")

    assert avatars.get_avatar(5, 99) == ("Elle (guild)", "https://cdn/guild.png")
    assert avatars.get_avatar(5, 12) == ("Elle", "https://cdn/global.png")
    assert avatars.get_avatar(6, 99) is None


def test_avatar_update_overwrites(avatars: UserAvatarCache):
    avatars.update_avatar(5, 99, "old", "https://cdn/old.png")
    avatars.update_avatar(5, 99, "new", "https://cdn/new.png")

    assert avatars.get_avatar(5, 99) == ("new", "https://cdn/new.png")
