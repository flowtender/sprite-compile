"""`DiscordSprite`, a `commands.Bot` that loads its extensions, syncs its application emojis, registers its slash
commands and keeps its username.

The steps are plain functions too (`sync_application_emojis`, `sync_commands`, `sync_username`), so a bot that doesn't
fit the base class can call them itself. A bot embeds `DiscordSettings` in its config as the `[discord]` table and
lists its extensions and the package holding its emoji images:

    class ChanceSprite(DiscordSprite):
        extension_names = ("chance_sprite.command_loader",)
        emoji_package = "chance_sprite.emojis"

`run()` loads the extensions before logging in, so a broken extension fails the start before READY and the release is
rolled back. Emojis are synced after login, so extensions read `self.app_emojis` when they render, not when they load;
until the sync, every lookup gets its fallback. Extensions add their commands to the global tree; `sync_commands`
copies them to the guild when one is configured.
"""

from __future__ import annotations

import logging
from importlib import resources
from typing import Annotated, Any

import discord
from discord import app_commands
from discord.ext import commands

from .config import ConfigSection, Setting
from .emojis import AppEmojis, sync_application_emojis

log = logging.getLogger(__name__)

ADDED = "sprite-compile 0.1.0"


class DiscordSettings(ConfigSection):
    guild_id: Annotated[
        int | None, Setting("Server to register the slash commands in; they show up there at once.", added=ADDED)
    ] = None
    global_commands: Annotated[
        bool,
        Setting(
            "Register the slash commands globally (every server and DM; Discord takes a while to propagate them). "
            "Turning it off removes previously registered global commands.",
            added=ADDED,
        ),
    ] = True
    username: Annotated[
        str | None,
        Setting("Bot username, applied at login when it differs (Discord allows 2 changes per hour).", added=ADDED),
    ] = None


async def sync_commands(tree: app_commands.CommandTree, *, guild_id: int | None, global_commands: bool) -> None:
    """Register the tree's global commands in the guild and/or globally. The global set is synced even when it's
    turned off, so commands registered by an earlier run are removed."""
    if guild_id is not None:
        guild = discord.Object(id=guild_id)
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
        log.info("Synced %d command(s) to guild %d", len(tree.get_commands(guild=guild)), guild_id)
    if not global_commands:
        tree.clear_commands(guild=None)
    await tree.sync()
    log.info("Synced %d global command(s)", len(tree.get_commands()))


async def sync_username(client: discord.Client, username: str | None) -> None:
    """Rename the bot when `username` is set and differs; a failure (e.g. Discord's rate limit) is only logged."""
    if client.user is None or username is None or client.user.name == username:
        return
    log.info("Changing username from %s to %s", client.user.name, username)
    try:
        await client.user.edit(username=username)
    except discord.HTTPException:
        log.warning("Could not change the username to %s", username, exc_info=True)


class DiscordSprite(commands.Bot):
    extension_names: tuple[str, ...] = ()
    emoji_package: str | None = None

    def __init__(self, settings: DiscordSettings, *, intents: discord.Intents, **options: Any) -> None:
        options.setdefault("command_prefix", commands.when_mentioned)  # slash commands only; prefix is never used
        super().__init__(intents=intents, **options)
        self.discord_settings = settings
        self.app_emojis = AppEmojis()

    async def load_extensions(self) -> None:
        for name in self.extension_names:
            await self.load_extension(name)

    async def setup_hook(self) -> None:
        if self.emoji_package is not None:
            self.app_emojis = await sync_application_emojis(self, resources.files(self.emoji_package))
        await sync_commands(
            self.tree, guild_id=self.discord_settings.guild_id, global_commands=self.discord_settings.global_commands
        )

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user and self.user.id)
        await sync_username(self, self.discord_settings.username)
