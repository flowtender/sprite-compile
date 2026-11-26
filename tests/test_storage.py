from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import msgspec
import pytest

from sprite_compile.codec import MessageCodec
from sprite_compile.storage import (
    CacheTable,
    DatabaseHandle,
    Table,
    UserAvatarStore,
    cache_dir,
    epoch_seconds,
    state_dir,
)


@dataclass
class Roll:
    label: str
    dice: list[int]
    rolled_at: datetime


ROLL = Roll("Agility + Pistols", [6, 5, 1], datetime(2026, 11, 20, 18, 30, tzinfo=UTC))


@pytest.fixture
def codec() -> MessageCodec:
    codec = MessageCodec()
    codec.register("Roll")(Roll)
    return codec


@pytest.fixture
def database(tmp_path: Path, codec: MessageCodec):
    handle = DatabaseHandle(tmp_path / "state.sqlite3", codec)
    yield handle
    handle.close()


def test_table_round_trips_dataclasses_across_reopen(tmp_path: Path, codec: MessageCodec):
    first = DatabaseHandle(tmp_path / "state.sqlite3", codec)
    Table(first, "rolls", int, Roll)[123456789012345678] = ROLL
    first.close()

    reopened = DatabaseHandle(tmp_path / "state.sqlite3", codec)
    assert Table(reopened, "rolls", int, Roll)[123456789012345678] == ROLL
    reopened.close()


def test_str_keyed_table_decodes_plain_values_by_hint(database: DatabaseHandle):
    seen_posts = Table(database, "seen_posts", str, dict[str, int])
    seen_posts["t3_abc"] = {"discord_post_id": 42}
    seen_posts["t3_xyz"] = {}

    assert seen_posts["t3_abc"] == {"discord_post_id": 42}
    assert list(seen_posts) == ["t3_abc", "t3_xyz"]
    assert len(seen_posts) == 2


def test_missing_row_is_none_from_get_and_keyerror_from_index(database: DatabaseHandle):
    rolls = Table(database, "rolls", int, Roll)

    assert rolls.get(1) is None
    with pytest.raises(KeyError):
        rolls[1]
    with pytest.raises(KeyError):
        del rolls[1]
    assert rolls.delete(1) is False


def test_seed_keeps_existing_row(database: DatabaseHandle):
    rolls = Table(database, "rolls", int, Roll)
    rolls[1] = ROLL
    rolls.seed(1, Roll("other", [], ROLL.rolled_at))
    rolls.seed(2, ROLL)

    assert rolls[1] == ROLL
    assert rolls[2] == ROLL


def test_opens_a_table_written_by_chance_sprite(tmp_path: Path, codec: MessageCodec):
    path = tmp_path / "chance_sprite.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.execute("CREATE TABLE message_records (record_id INTEGER PRIMARY KEY, payload BLOB NOT NULL)")
    payload = msgspec.msgpack.encode({"type": "Roll", "label": "old", "dice": [3], "rolled_at": ROLL.rolled_at})
    legacy.execute("INSERT INTO message_records VALUES (?, ?)", (7, payload))
    legacy.commit()
    legacy.close()

    database = DatabaseHandle(path, codec)
    assert Table(database, "message_records", int, Roll)[7] == Roll("old", [3], ROLL.rolled_at)
    database.close()


def test_rejects_table_names_that_are_not_identifiers(database: DatabaseHandle):
    with pytest.raises(ValueError):
        Table(database, "rolls; DROP TABLE rolls", int, Roll)


def test_cache_hides_expired_rows(database: DatabaseHandle):
    cache = CacheTable(database, "webhooks", int, Roll)
    cache.set(1, ROLL, expires_at=epoch_seconds() + 60)
    cache.set(2, ROLL, expires_at=epoch_seconds() - 1)

    assert cache.get(1) == ROLL
    assert cache.get(2) is None
    assert list(cache) == [1]
    assert len(cache) == 1


def test_cache_deletes_expired_rows_when_opened(database: DatabaseHandle):
    CacheTable(database, "webhooks", int, Roll).set(1, ROLL, expires_at=epoch_seconds() - 1)

    CacheTable(database, "webhooks", int, Roll)
    (rows,) = database.conn.execute("SELECT COUNT(*) FROM webhooks").fetchone()
    assert rows == 0


def test_cache_set_refreshes_expiry(database: DatabaseHandle):
    cache = CacheTable(database, "webhooks", int, Roll)
    cache.set(1, ROLL, expires_at=epoch_seconds() - 1)
    cache.set(1, ROLL, expires_at=epoch_seconds() + 60)

    assert cache.get(1) == ROLL


def test_cache_rows_need_an_expiry(database: DatabaseHandle):
    with pytest.raises(TypeError):
        CacheTable(database, "webhooks", int, Roll)[1] = ROLL


def test_avatar_falls_back_to_global_row(database: DatabaseHandle):
    avatars = UserAvatarStore(database)
    avatars.update_avatar(5, 0, "Elle", "https://cdn/global.png")
    avatars.update_avatar(5, 99, "Elle (guild)", "https://cdn/guild.png")

    assert avatars.get_avatar(5, 99) == ("Elle (guild)", "https://cdn/guild.png")
    assert avatars.get_avatar(5, 12) == ("Elle", "https://cdn/global.png")
    assert avatars.get_avatar(6, 99) is None


def test_avatar_update_overwrites(database: DatabaseHandle):
    avatars = UserAvatarStore(database)
    avatars.update_avatar(5, 99, "old", "https://cdn/old.png")
    avatars.update_avatar(5, 99, "new", "https://cdn/new.png")

    assert avatars.get_avatar(5, 99) == ("new", "https://cdn/new.png")


def test_instance_data_dir_holds_both_databases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("SPRITE_DATA_DIR", str(tmp_path))

    assert state_dir("chance-sprite") == tmp_path
    assert cache_dir("chance-sprite") == tmp_path


def test_development_keeps_cache_apart_from_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SPRITE_DATA_DIR", raising=False)

    assert state_dir("chance-sprite") != cache_dir("chance-sprite")
