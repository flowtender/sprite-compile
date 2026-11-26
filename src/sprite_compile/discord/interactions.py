"""Interaction helpers, from chance's `InteractionContext`: deferring, ephemeral followup menus that remember the
message they were opened from, and editing a message the bot sent earlier.

Editing goes through the message's cached `InteractionMessage` first, then through a partial message with the bot
token. The cached handle edits through the interaction's webhook, which also works where the bot can't see the
channel (group DMs, DMs with other users); the partial message only works in channels the bot can access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, TypeGuard, runtime_checkable

import discord

from ..codec import library_type
from ..storage import DatabaseHandle, ExpiringTable, epoch_seconds

log = logging.getLogger(__name__)

# A followup can only be edited through its interaction's webhook, whose token lasts 15 minutes; 10 s of margin.
FOLLOWUP_LIFETIME_SECONDS = 15 * 60 - 10


@library_type("WebhookHandle")
@dataclass(frozen=True)
class WebhookHandle:
    message_id: int
    webhook_id: int
    expires_at: int
    original_target: int | None  # the message the followup was opened from


class MessageHandles:
    """The message objects of the bot's interaction responses and followups, by message ID, for this process's lifetime.
    They edit through their interaction's webhook, which also works where the bot can't see the channel. They can't be
    stored: after a restart, edits fall back to partial messages."""

    def __init__(self) -> None:
        self.by_id: dict[int, discord.InteractionMessage | discord.WebhookMessage] = {}

    def remember(self, response: discord.InteractionCallbackResponse) -> None:
        if isinstance(response.resource, discord.InteractionMessage):
            self.remember_message(response.resource)

    def remember_message(self, message: discord.InteractionMessage | discord.WebhookMessage) -> None:
        """E.g. a followup the bot sent, or a component interaction's `original_response()`: the message the component
        is on."""
        self.by_id[message.id] = message

    def get(self, message_id: int) -> discord.InteractionMessage | discord.WebhookMessage | None:
        return self.by_id.get(message_id)


class WebhookHandles:
    """The bot's ephemeral followups by message ID, in the cache database until their webhook token expires."""

    TABLE = "webhook_handles"

    def __init__(self, database: DatabaseHandle) -> None:
        self.table = ExpiringTable(database, self.TABLE, int, WebhookHandle)

    def remember(self, handle: WebhookHandle) -> None:
        self.table.set(handle.message_id, handle, expires_at=handle.expires_at)

    def get(self, message_id: int) -> WebhookHandle | None:
        return self.table.get(message_id)


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


def log_edit_failure(attempt: str, message_id: int, error: Exception) -> None:
    """A deleted message gets one line; anything else gets its traceback."""
    if isinstance(error, discord.NotFound) and error.code == UNKNOWN_MESSAGE:
        log.info("%s %d failed: the message no longer exists", attempt, message_id)
    else:
        log.info("%s %d failed", attempt, message_id, exc_info=error)


async def edit_message(
    interaction: discord.Interaction, message_id: int, handles: MessageHandles, **edit_args: Any
) -> bool:
    """Edit a message the bot sent earlier: through its cached handle, else as a partial message in the interaction's
    channel. Returns whether either worked; failures are logged. Doesn't respond to `interaction`."""
    handle = handles.get(message_id)
    if handle is None:
        log.info("Message key not cached: %d", message_id)
    else:
        try:
            await handle.edit(**edit_args)
            log.info("Edited via cached message")
            return True
        except Exception as error:
            log_edit_failure("Editing via cached message", message_id, error)

    channel = interaction.channel
    if not has_get_partial_message(channel):
        log.info("Interaction channel is not partial-messageable: %r", type(channel))
        return False
    try:
        await channel.get_partial_message(message_id).edit(**edit_args)
    except Exception as error:
        log_edit_failure("Editing via partial message", message_id, error)
        return False
    log.info("Edited via partial message")
    return True


async def send_ephemeral_followup(
    interaction: discord.Interaction, webhook_handles: WebhookHandles, **send_args: Any
) -> discord.WebhookMessage:
    """Send an ephemeral followup (e.g. a menu) and remember which message it was opened from."""
    original_target = interaction.message.id if interaction.message else None
    message = await interaction.followup.send(wait=True, ephemeral=True, **send_args)
    handle = WebhookHandle(
        message_id=message.id,
        webhook_id=interaction.followup.id,
        expires_at=epoch_seconds() + FOLLOWUP_LIFETIME_SECONDS,
        original_target=original_target,
    )
    webhook_handles.remember(handle)
    return message


async def update_menu(interaction: discord.Interaction, **edit_args: Any) -> bool:
    """Edit the message whose component was used (e.g. a followup menu) through the interaction's webhook. Returns
    False when the interaction has no message; Discord's errors are raised."""
    if interaction.message is None:
        log.error("couldn't edit interaction message")
        return False
    await interaction.followup.edit_message(interaction.message.id, **edit_args)
    return True
