from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from sprite_compile.config import check_settings, load_config
from sprite_compile.discord_sprite import DiscordSettings, DiscordSprite, sync_username

APPLICATION_ID = 1000
GUILD_ID = 2000


class PingSprite(DiscordSprite):
    extensions = ("discord_fixtures.ping",)


def make_bot(**settings) -> PingSprite:
    bot = PingSprite(DiscordSettings(**settings), intents=discord.Intents.none(), application_id=APPLICATION_ID)
    bot.http.bulk_upsert_global_commands = AsyncMock(return_value=[])
    bot.http.bulk_upsert_guild_commands = AsyncMock(return_value=[])
    return bot


def global_names(bot: PingSprite) -> list[str]:
    bot.http.bulk_upsert_global_commands.assert_awaited_once()
    return [command["name"] for command in bot.http.bulk_upsert_global_commands.await_args.kwargs["payload"]]


def guild_names(bot: PingSprite) -> list[str]:
    bot.http.bulk_upsert_guild_commands.assert_awaited_once()
    args = bot.http.bulk_upsert_guild_commands.await_args
    assert args.args == (APPLICATION_ID, GUILD_ID)
    return [command["name"] for command in args.kwargs["payload"]]


def test_settings_are_all_optional_and_documented(tmp_path):
    check_settings(DiscordSettings)
    assert load_config(DiscordSettings, tmp_path / "missing.toml") == DiscordSettings()


async def test_extensions_commands_are_registered_globally():
    bot = make_bot()
    await bot.setup_hook()

    assert global_names(bot) == ["ping"]
    bot.http.bulk_upsert_guild_commands.assert_not_awaited()


class EmojiPingSprite(PingSprite):
    emoji_package = "discord_fixtures.emojis"


async def test_package_emojis_are_ready_when_extensions_load():
    bot = EmojiPingSprite(DiscordSettings(), intents=discord.Intents.none(), application_id=APPLICATION_ID)
    bot.http.bulk_upsert_global_commands = AsyncMock(return_value=[])
    dice = discord.PartialEmoji(name="dice", id=7)
    bot.fetch_application_emojis = AsyncMock(side_effect=[[], [dice]])
    bot.create_application_emoji = AsyncMock(return_value=dice)

    await bot.setup_hook()

    bot.create_application_emoji.assert_awaited_once()
    assert bot.create_application_emoji.await_args.kwargs["name"] == "dice"
    assert bot.emojis_at_extension_load.get("dice", "🎲") == "<:dice:7>"


async def test_guild_only_removes_previous_global_commands():
    bot = make_bot(guild_id=GUILD_ID, global_commands=False)
    await bot.setup_hook()

    assert guild_names(bot) == ["ping"]
    assert global_names(bot) == []


async def test_guild_and_global_register_both():
    bot = make_bot(guild_id=GUILD_ID)
    await bot.setup_hook()

    assert guild_names(bot) == ["ping"]
    assert global_names(bot) == ["ping"]


def fake_client(name: str, edit: AsyncMock) -> discord.Client:
    return SimpleNamespace(user=SimpleNamespace(name=name, edit=edit))  # type: ignore[return-value]


async def test_username_is_changed_when_it_differs():
    edit = AsyncMock()
    await sync_username(fake_client("old-name", edit), "chance")
    edit.assert_awaited_once_with(username="chance")


@pytest.mark.parametrize("username", ["chance", None])
async def test_username_is_left_alone_when_matching_or_unset(username):
    edit = AsyncMock()
    await sync_username(fake_client("chance", edit), username)
    edit.assert_not_awaited()


async def test_rate_limited_rename_is_logged_not_raised(caplog: pytest.LogCaptureFixture):
    response = SimpleNamespace(status=429, reason="Too Many Requests")
    edit = AsyncMock(side_effect=discord.HTTPException(response, "You are changing your username too fast"))

    await sync_username(fake_client("old-name", edit), "chance")
    assert "Could not change the username to chance" in caplog.text
