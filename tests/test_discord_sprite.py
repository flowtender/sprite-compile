from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands

from button_fixtures.buttons import EdgeButton, MenuButton, RollButton
from sprite_compile.config import check_settings, load_config
from sprite_compile.discord.sprite import DiscordSettings, DiscordSprite, sync_username

APPLICATION_ID = 1000
GUILD_ID = 2000


class PingSprite(DiscordSprite):
    extension_names = ("discord_fixtures.ping",)


def make_bot(bot_type: type[DiscordSprite] = PingSprite, **settings) -> DiscordSprite:
    bot = bot_type(
        DiscordSettings(**settings), token="abc.def", intents=discord.Intents.none(), application_id=APPLICATION_ID
    )
    bot.http.bulk_upsert_global_commands = AsyncMock(return_value=[])
    bot.http.bulk_upsert_guild_commands = AsyncMock(return_value=[])
    return bot


async def start(bot: DiscordSprite) -> None:
    """What `run()` and discord.py do before the bot is ready, minus the Discord login."""
    await bot.prepare()
    await bot.setup_hook()


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
    await start(bot)

    assert global_names(bot) == ["ping"]
    bot.http.bulk_upsert_guild_commands.assert_not_awaited()


class EmojiPingSprite(PingSprite):
    emoji_package = "discord_fixtures.emojis"


async def test_package_emojis_are_synced():
    bot = make_bot(EmojiPingSprite)
    dice = discord.PartialEmoji(name="dice", id=7)
    bot.fetch_application_emojis = AsyncMock(side_effect=[[], [dice]])
    bot.create_application_emoji = AsyncMock(return_value=dice)
    assert bot.app_emojis.get("dice", "🎲") == "🎲"

    await start(bot)

    bot.create_application_emoji.assert_awaited_once()
    assert bot.create_application_emoji.await_args.kwargs["name"] == "dice"
    assert bot.app_emojis.get("dice", "🎲") == "<:dice:7>"


class BrokenSprite(DiscordSprite):
    extension_names = ("discord_fixtures.broken",)


async def test_broken_extension_fails_prepare():
    with pytest.raises(commands.ExtensionFailed, match="Duplicate command"):
        await make_bot(BrokenSprite).prepare()


def registered_dynamic_items(bot: DiscordSprite) -> set[type]:
    return set(bot._connection._view_store._dynamic_items.values())


async def test_persistent_buttons_are_registered_again_after_a_reconnect():
    bot = make_bot()
    await start(bot)
    bot._connection.clear()  # the part of serve()'s clear() that drops them; the rest needs a real start first
    await bot.setup_hook()
    assert {EdgeButton, MenuButton} <= registered_dynamic_items(bot)
    assert RollButton not in registered_dynamic_items(bot)


async def test_serve_reopens_the_client_for_the_next_attempt():
    bot = make_bot()
    closed_at_login = []

    async def static_login(token: str) -> None:
        closed_at_login.append(bot.is_closed())
        raise discord.LoginFailure("Improper token has been passed.")

    bot.http.static_login = static_login

    for _ in range(2):
        with pytest.raises(discord.LoginFailure):
            await bot.serve()
    assert closed_at_login == [False, False]
    assert bot.is_closed()


async def test_serve_connects_through_a_fresh_connector_after_a_failed_attempt():
    bot = make_bot()
    connector_closed_at_request = []

    async def request(route: object, **kwargs: object) -> None:
        connector_closed_at_request.append(bot.http.connector.closed)
        raise OSError("name resolution failed")

    bot.http.request = request

    for _ in range(2):
        with pytest.raises(OSError):
            await bot.serve()
    assert connector_closed_at_request == [False, False]


async def test_guild_only_removes_previous_global_commands():
    bot = make_bot(guild_id=GUILD_ID, global_commands=False)
    await start(bot)

    assert guild_names(bot) == ["ping"]
    assert global_names(bot) == []


async def test_guild_and_global_register_both():
    bot = make_bot(guild_id=GUILD_ID)
    await start(bot)

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
