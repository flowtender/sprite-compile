"""Application emojis uploaded from a bot's package assets, with a fallback for every name the application lacks.

Emojis are matched by name (the file name without its extension), and the server wins: an emoji the application already
has is never replaced. To replace an image, delete its emoji in the Discord Developer Portal and restart. Files are
recognised as images by their content, not their extension. Nothing here fails the bot; uploads and lookups that don't
work are logged and skipped, and messages use each name's fallback instead.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from importlib.resources.abc import Traversable
from io import BytesIO
from pathlib import PurePath

import discord
from PIL import Image, UnidentifiedImageError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppEmojis:
    """The application's emojis by name, as message markup ("<:name:id>")."""

    markup: Mapping[str, str] = field(default_factory=dict)

    def get(self, name: str, fallback: str) -> str:
        return self.markup.get(name, fallback)


def emoji_images(assets: Traversable) -> Iterator[tuple[str, bytes]]:
    """(emoji name, image bytes) for every file directly in `assets` that Pillow recognises as an image."""
    for entry in sorted(assets.iterdir(), key=lambda entry: entry.name):
        if not entry.is_file():
            continue
        data = entry.read_bytes()
        try:
            with Image.open(BytesIO(data)) as image:
                image.verify()
        except UnidentifiedImageError:
            continue  # not an image, e.g. the package's __init__.py
        except Exception:  # Pillow raises assorted types for damaged files
            log.warning("Skipping the damaged image %s", entry.name, exc_info=True)
            continue
        yield PurePath(entry.name).stem, data


async def fetch_emojis(client: discord.Client) -> dict[str, discord.Emoji] | None:
    try:
        return {emoji.name: emoji for emoji in await client.fetch_application_emojis()}
    except discord.HTTPException:
        log.warning("Could not fetch the application's emojis", exc_info=True)
        return None


async def sync_application_emojis(client: discord.Client, assets: Traversable) -> AppEmojis:
    """Upload every image in `assets` the application has no emoji of that name for; return the server's emojis
    afterwards. `assets` is a folder: `importlib.resources.files("my_bot.emojis")` or a `Path`."""
    emojis = await fetch_emojis(client)
    if emojis is None:
        log.warning("Uploading no emojis; messages use the fallbacks")
        return AppEmojis()
    log.info("The application has %d emoji(s)", len(emojis))

    attempted = 0
    for name, image in emoji_images(assets):
        if name in emojis:
            continue
        attempted += 1
        try:
            emojis[name] = await client.create_application_emoji(name=name, image=image)
        except discord.HTTPException:
            log.warning("Could not upload the emoji %s", name, exc_info=True)
            continue
        log.info("Uploaded the emoji %s", name)

    # A failed upload may still have gone through on Discord's side, so the server's list is the truth.
    if attempted and (refreshed := await fetch_emojis(client)) is not None:
        emojis = refreshed
    log.info("Emoji sync done: %d upload(s) attempted, %d emoji(s) available", attempted, len(emojis))
    return AppEmojis({name: str(emoji) for name, emoji in emojis.items()})
