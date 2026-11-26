from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import msgspec
import pytest

from sprite_compile.codec import MessageCodec
from sprite_compile.storage import DatabaseHandle, ExpiringTable, Table, epoch_seconds, open_databases


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


def test_row_that_doesnt_decode_reads_as_missing(database: DatabaseHandle, caplog: pytest.LogCaptureFixture):
    rolls = Table(database, "rolls", int, Roll)
    old_shape = msgspec.msgpack.encode({"type": "Roll", "label": "from before dice were stored"})
    database.conn.execute("INSERT INTO rolls VALUES (?, ?)", (7, old_shape))

    assert rolls.get(7) is None
    assert "Row 7 of rolls doesn't decode" in caplog.text


def test_expiring_table_hides_expired_rows(database: DatabaseHandle):
    table = ExpiringTable(database, "webhooks", int, Roll)
    table.set(1, ROLL, expires_at=epoch_seconds() + 60)
    table.set(2, ROLL, expires_at=epoch_seconds() - 1)

    assert table.get(1) == ROLL
    assert table.get(2) is None
    assert list(table) == [1]
    assert len(table) == 1


def test_expiring_table_deletes_expired_rows_when_opened(database: DatabaseHandle):
    ExpiringTable(database, "webhooks", int, Roll).set(1, ROLL, expires_at=epoch_seconds() - 1)

    ExpiringTable(database, "webhooks", int, Roll)
    (rows,) = database.conn.execute("SELECT COUNT(*) FROM webhooks").fetchone()
    assert rows == 0


def test_expiring_set_refreshes_expiry(database: DatabaseHandle):
    table = ExpiringTable(database, "webhooks", int, Roll)
    table.set(1, ROLL, expires_at=epoch_seconds() - 1)
    table.set(1, ROLL, expires_at=epoch_seconds() + 60)

    assert table.get(1) == ROLL


def test_expiring_rows_need_an_expiry(database: DatabaseHandle):
    with pytest.raises(TypeError):
        ExpiringTable(database, "webhooks", int, Roll)[1] = ROLL


def test_state_and_cache_databases_share_the_deployed_data_dir(
    tmp_path: Path, codec: MessageCodec, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SPRITE_DATA_DIR", str(tmp_path))
    databases = open_databases("chance-sprite", codec)
    databases.close()
    assert sorted(path.name for path in tmp_path.glob("*.sqlite3")) == ["cache.sqlite3", "state.sqlite3"]
