"""SQLite storage: typed tables in a state database, a separate cache database, and tables whose rows expire.

Values are stored as msgpack of the codec's plain data and rebuilt from the table's value type; a row that doesn't
decode (e.g. written by an older version of a class) is logged and reads as missing. The cache database only holds
what can be lost (rows expire or are refreshed in use), so it is always safe to delete.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import msgspec

from .codec import MessageCodec
from .paths import cache_dir, state_dir

log = logging.getLogger(__name__)

STATE_DATABASE = "state.sqlite3"
CACHE_DATABASE = "cache.sqlite3"

TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
KEY_COLUMN_TYPES = {int: "INTEGER", str: "TEXT"}


def epoch_seconds() -> int:
    return int(time.time())


class DatabaseHandle:
    def __init__(self, path: Path, codec: MessageCodec) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.codec = codec
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")

    def close(self) -> None:
        self.conn.close()

    def encode(self, value: Any) -> bytes:
        return msgspec.msgpack.encode(self.codec.dict_from_dataclass(value))

    def decode(self, payload: bytes, hint: Any) -> Any:
        return self.codec.decode_with_hint(msgspec.msgpack.decode(payload), hint)


@dataclass
class Databases:
    state: DatabaseHandle
    cache: DatabaseHandle

    def close(self) -> None:
        self.state.close()
        self.cache.close()


def open_databases(app_name: str, codec: MessageCodec) -> Databases:
    """The instance's state and cache databases (both in `$SPRITE_DATA_DIR` when deployed)."""
    return Databases(
        state=DatabaseHandle(state_dir(app_name) / STATE_DATABASE, codec),
        cache=DatabaseHandle(cache_dir(app_name) / CACHE_DATABASE, codec),
    )


def checked_table_name(name: str) -> str:
    # Table names are interpolated into SQL, so only plain identifiers are allowed.
    if not TABLE_NAME.fullmatch(name):
        raise ValueError(f"Invalid table name: {name!r}")
    return name


class KeyedTable[K: (int, str), V](MutableMapping[K, V]):
    """Rows of `record_id` (int or str) and a codec-encoded `payload`, plus whatever `extra_columns` subclasses need."""

    def __init__(
        self, database: DatabaseHandle, name: str, key_type: type[K], value_type: Any, extra_columns: str = ""
    ) -> None:
        self.database = database
        self.name = checked_table_name(name)
        self.value_type = value_type
        database.conn.execute(
            f"CREATE TABLE IF NOT EXISTS {self.name} ("
            f"record_id {KEY_COLUMN_TYPES[key_type]} PRIMARY KEY, payload BLOB NOT NULL{extra_columns})"
        )

    def decode(self, key: K, payload: bytes) -> V:
        """The row's value; a row that doesn't decode is logged and raises KeyError, like a missing one."""
        try:
            return self.database.decode(payload, self.value_type)
        except Exception:
            log.warning("Row %r of %s doesn't decode; treating it as missing", key, self.name, exc_info=True)
            raise KeyError(key) from None

    def delete(self, key: K) -> bool:
        """Delete the row if there is one; returns whether there was."""
        cursor = self.database.conn.execute(f"DELETE FROM {self.name} WHERE record_id=?", (key,))
        return cursor.rowcount > 0

    def __delitem__(self, key: K, /) -> None:
        if not self.delete(key):
            raise KeyError(key)


class Table[K: (int, str), V](KeyedTable[K, V]):
    """A state table of values of one type. `get` returns None for a missing row; `table[key]` raises KeyError."""

    def __init__(self, database: DatabaseHandle, name: str, key_type: type[K], value_type: Any) -> None:
        super().__init__(database, name, key_type, value_type)

    def set(self, key: K, value: V) -> None:
        self.database.conn.execute(
            f"INSERT INTO {self.name}(record_id, payload) VALUES(?, ?) "
            "ON CONFLICT(record_id) DO UPDATE SET payload=excluded.payload",
            (key, self.database.encode(value)),
        )

    def seed(self, key: K, value: V) -> None:
        """Insert the row only if there is none for `key` yet."""
        self.database.conn.execute(
            f"INSERT INTO {self.name}(record_id, payload) VALUES(?, ?) ON CONFLICT(record_id) DO NOTHING",
            (key, self.database.encode(value)),
        )

    def __getitem__(self, key: K, /) -> V:
        row = self.database.conn.execute(f"SELECT payload FROM {self.name} WHERE record_id=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return self.decode(key, row[0])

    def __setitem__(self, key: K, value: V, /) -> None:
        self.set(key, value)

    def __len__(self) -> int:
        (count,) = self.database.conn.execute(f"SELECT COUNT(*) FROM {self.name}").fetchone()
        return count

    def __iter__(self) -> Iterator[K]:
        rows = self.database.conn.execute(f"SELECT record_id FROM {self.name} ORDER BY record_id").fetchall()
        return (key for (key,) in rows)


class ExpiringTable[K: (int, str), V](KeyedTable[K, V]):
    """A table whose every row expires at an epoch second, after which it reads as missing. Expired rows are
    deleted when the table is opened and by `purge_expired()`."""

    def __init__(self, database: DatabaseHandle, name: str, key_type: type[K], value_type: Any) -> None:
        super().__init__(database, name, key_type, value_type, extra_columns=", expires_at INTEGER NOT NULL")
        self.purge_expired()

    def purge_expired(self) -> int:
        """Delete expired rows; returns how many."""
        cursor = self.database.conn.execute(f"DELETE FROM {self.name} WHERE expires_at<=?", (epoch_seconds(),))
        return cursor.rowcount

    def set(self, key: K, value: V, *, expires_at: int) -> None:
        self.database.conn.execute(
            f"INSERT INTO {self.name}(record_id, payload, expires_at) VALUES(?, ?, ?) "
            "ON CONFLICT(record_id) DO UPDATE SET payload=excluded.payload, expires_at=excluded.expires_at",
            (key, self.database.encode(value), int(expires_at)),
        )

    def __getitem__(self, key: K, /) -> V:
        row = self.database.conn.execute(
            f"SELECT payload FROM {self.name} WHERE record_id=? AND expires_at>?", (key, epoch_seconds())
        ).fetchone()
        if row is None:
            raise KeyError(key)
        return self.decode(key, row[0])

    def __setitem__(self, key: K, value: V, /) -> None:
        raise TypeError("Expiring rows need an expiry: use set(key, value, expires_at=...)")

    def __len__(self) -> int:
        (count,) = self.database.conn.execute(
            f"SELECT COUNT(*) FROM {self.name} WHERE expires_at>?", (epoch_seconds(),)
        ).fetchone()
        return count

    def __iter__(self) -> Iterator[K]:
        rows = self.database.conn.execute(
            f"SELECT record_id FROM {self.name} WHERE expires_at>? ORDER BY record_id", (epoch_seconds(),)
        ).fetchall()
        return (key for (key,) in rows)
