"""Interaction helpers, from chance's `InteractionContext`: deferring, ephemeral followup menus, and editing a message
the bot sent earlier.

Editing goes through an interaction token stored for the message first, then through a partial message with the bot
token. The token edits through the interaction's webhook, which also works where the bot can't see the channel (group
DMs, DMs with other users); the partial message only works in channels the bot can access. Tokens are kept in the cache
database until they expire, so they keep working across a restart.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeGuard, runtime_checkable

import discord
from discord import ui

from ..codec import library_type
from ..storage import DatabaseHandle, ExpiringTable, epoch_seconds

log = logging.getLogger(__name__)

# An interaction's webhook token lasts 15 minutes from the interaction; its responses and followups can only be edited
# (or deleted) through it. 10 s of margin.
TOKEN_LIFETIME_SECONDS = 15 * 60 - 10


@library_type("MenuOrigin")
@dataclass(frozen=True)
class MenuOrigin:
    """Where an ephemeral followup menu came from, to build it again after a restart."""

    message_id: int | None  # the message whose component opened it
    user_id: int  # who opened it


@library_type("MessageHandle")
@dataclass(frozen=True)
class MessageHandle:
    """An interaction token that can edit (or delete) a message, until `expires_at`."""

    token: str
    expires_at: int
    menu: MenuOrigin | None = None  # set for an ephemeral followup menu, whose components die with the process


type MenuBuilder = Callable[[MenuOrigin], ui.View | ui.LayoutView | None]


class MessageHandles:
    """Interaction tokens for the bot's messages, by message ID, in the cache database until they expire. Whoever can
    read the cache database can edit or delete those messages until then."""

    TABLE = "message_handles"

    def __init__(self, database: DatabaseHandle) -> None:
        self.table = ExpiringTable(database, self.TABLE, int, MessageHandle)

    def remember(
        self, interaction: discord.Interaction, message_id: int, *, menu: MenuOrigin | None = None
    ) -> MessageHandle:
        """`message_id`: the interaction's response, one of its followups, or for a component interaction the message
        the component is on."""
        expires_at = int(interaction.created_at.timestamp()) + TOKEN_LIFETIME_SECONDS
        handle = MessageHandle(interaction.token, expires_at, menu)
        self.table.set(message_id, handle, expires_at=expires_at)
        return handle

    def get(self, message_id: int) -> MessageHandle | None:
        return self.table.get(message_id)

    async def restore_menus(self, client: discord.Client, build_menu: MenuBuilder) -> None:
        """Redraw the menus an earlier process left open with views from `build_menu`, so their components work again
        in this one; each still deletes itself when its token expires. A menu `build_menu` gives None for is deleted."""
        for message_id, handle in list(self.table.items()):
            if handle.menu is None:
                continue
            webhook = handle_webhook(client, handle)
            try:
                view = build_menu(handle.menu)
                if view is None:
                    await webhook.delete_message(message_id)
                    del self.table[message_id]
                    log.info("Deleted menu %d: nothing to rebuild it from", message_id)
                    continue
                message = await webhook.edit_message(message_id, view=view)
            except Exception as error:
                log_message_failure("Restoring menu", message_id, error)
                continue
            await delete_when_expired(message, handle)
            log.info("Restored menu %d", message_id)


def handle_webhook(client: discord.Client, handle: MessageHandle) -> discord.Webhook:
    """The interaction webhook `handle`'s token belongs to."""
    assert client.application_id is not None
    return discord.Webhook.partial(client.application_id, handle.token, client=client)


async def delete_when_expired(message: discord.WebhookMessage, handle: MessageHandle) -> None:
    """Delete `message` in the background just before `handle`'s token expires, after which it no longer could."""
    await message.delete(delay=handle.expires_at - epoch_seconds())


@runtime_checkable
class PartialMessageable(Protocol):
    def get_partial_message(self, message_id: int, /) -> discord.PartialMessage: ...


def has_get_partial_message(channel: object) -> TypeGuard[PartialMessageable]:
    return isinstance(channel, PartialMessageable)


async def defer_if_needed(interaction: discord.Interaction) -> None:
    """Acknowledge the interaction unless something already responded. A failure is only logged (with the caller's
    stack): the interaction may have been answered in the meantime, expired, or the network may be down."""
    if interaction.response.is_done():
        return
    try:
        await interaction.response.defer()
    except Exception:
        log.info("Could not defer the interaction", exc_info=True, stack_info=True)


UNKNOWN_MESSAGE = 10008  # Discord's error code for a message that doesn't exist (e.g. deleted)


def log_message_failure(attempt: str, message_id: int, error: Exception) -> None:
    """A deleted message gets one line; anything else gets its traceback."""
    if isinstance(error, discord.NotFound) and error.code == UNKNOWN_MESSAGE:
        log.info("%s %d failed: the message no longer exists", attempt, message_id)
    else:
        log.info("%s %d failed", attempt, message_id, exc_info=error)


async def edit_message(
    interaction: discord.Interaction, message_id: int, handles: MessageHandles, **edit_args: Any
) -> bool:
    """Edit a message the bot sent earlier: through its stored interaction token, else as a partial message in the
    interaction's channel. Returns whether either worked; failures are logged. Doesn't respond to `interaction`."""
    handle = handles.get(message_id)
    if handle is None:
        log.info("No interaction token stored for message %d", message_id)
    else:
        try:
            await handle_webhook(interaction.client, handle).edit_message(message_id, **edit_args)
            log.info("Edited via stored interaction token")
            return True
        except Exception as error:
            log_message_failure("Editing via stored interaction token", message_id, error)

    channel = interaction.channel
    if not has_get_partial_message(channel):
        log.info("Interaction channel is not partial-messageable: %r", type(channel))
        return False
    try:
        await channel.get_partial_message(message_id).edit(**edit_args)
    except Exception as error:
        log_message_failure("Editing via partial message", message_id, error)
        return False
    log.info("Edited via partial message")
    return True


async def send_ephemeral_followup(
    interaction: discord.Interaction, handles: MessageHandles, **send_args: Any
) -> discord.WebhookMessage:
    """Send an ephemeral followup (e.g. a menu) that deletes itself when its interaction token is about to expire,
    since after that its components can no longer edit anything. One still open when the process ends is rebuilt at the
    next start (`MessageHandles.restore_menus`)."""
    message = await interaction.followup.send(wait=True, ephemeral=True, **send_args)
    origin = MenuOrigin(interaction.message.id if interaction.message else None, interaction.user.id)
    handle = handles.remember(interaction, message.id, menu=origin)
    await delete_when_expired(message, handle)
    return message


async def update_menu(interaction: discord.Interaction, **edit_args: Any) -> bool:
    """Edit the message whose component was used (e.g. a followup menu) through the interaction's webhook. Returns
    False when the interaction has no message; Discord's errors are raised."""
    if interaction.message is None:
        log.error("couldn't edit interaction message")
        return False
    await interaction.followup.edit_message(interaction.message.id, **edit_args)
    return True
