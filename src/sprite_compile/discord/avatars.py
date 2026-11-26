"""Discord users' display names and avatars as last seen, for showing who rolled or posted something."""

from __future__ import annotations

from ..storage import DatabaseHandle, epoch_seconds


class UserAvatarCache:
    """Each user's display name and avatar URL as last seen, per guild (guild 0: outside any guild). Lives in the
    cache database: rows are refreshed on every interaction, and a missing row reads as None."""

    TABLE = "identity_cache"

    def __init__(self, database: DatabaseHandle) -> None:
        self.database = database
        database.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.TABLE} (
                user_id     INTEGER NOT NULL,
                guild_id    INTEGER NOT NULL,
                name        TEXT NOT NULL,
                avatar_url  TEXT NOT NULL,
                updated_at  INTEGER NOT NULL,
                PRIMARY KEY (user_id, guild_id)
            )
            """
        )

    def get_avatar(self, user_id: int, guild_id: int = 0) -> tuple[str, str] | None:
        """(name, avatar URL) in the guild, falling back to the user's guild-0 row."""
        return self.database.conn.execute(
            f"SELECT name, avatar_url FROM {self.TABLE} WHERE user_id=? AND guild_id IN (?, 0) "
            "ORDER BY guild_id=0 LIMIT 1",
            (user_id, guild_id),
        ).fetchone()

    def update_avatar(self, user_id: int, guild_id: int, name: str, avatar_url: str) -> None:
        self.database.conn.execute(
            f"INSERT INTO {self.TABLE}(user_id, guild_id, name, avatar_url, updated_at) VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, guild_id) DO UPDATE SET "
            "name=excluded.name, avatar_url=excluded.avatar_url, updated_at=excluded.updated_at",
            (user_id, guild_id, name, avatar_url, epoch_seconds()),
        )
