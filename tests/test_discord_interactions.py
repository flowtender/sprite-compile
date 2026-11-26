from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from sprite_compile.codec import MessageCodec
from sprite_compile.discord.interactions import (
    FOLLOWUP_LIFETIME_SECONDS,
    MessageHandles,
    WebhookHandle,
    WebhookHandles,
    defer_if_needed,
    edit_message,
    send_ephemeral_followup,
    update_menu,
)
from sprite_compile.storage import DatabaseHandle, epoch_seconds

ROLL_MESSAGE = 300


class FakeChannel:
    """A channel the bot can reach with its own token."""

    def __init__(self, edit: AsyncMock) -> None:
        self.partial = SimpleNamespace(edit=edit)

    def get_partial_message(self, message_id: int, /) -> SimpleNamespace:
        assert message_id == ROLL_MESSAGE
        return self.partial


def fake_interaction(*, done: bool = False, channel: object = None, message_id: int | None = None) -> Mock:
    interaction = Mock()
    interaction.response.is_done.return_value = done
    interaction.response.defer = AsyncMock()
    interaction.channel = channel
    interaction.message = SimpleNamespace(id=message_id) if message_id is not None else None
    interaction.followup.id = 77
    interaction.followup.send = AsyncMock(return_value=SimpleNamespace(id=500))
    interaction.followup.edit_message = AsyncMock()
    return interaction


def handles_with(edit: AsyncMock) -> MessageHandles:
    handles = MessageHandles()
    handles.by_id[ROLL_MESSAGE] = SimpleNamespace(id=ROLL_MESSAGE, edit=edit)  # type: ignore[assignment]
    return handles


def http_error() -> discord.HTTPException:
    return discord.HTTPException(SimpleNamespace(status=401, reason="Unauthorized"), "Invalid Webhook Token")


@pytest.fixture
def webhook_handles(tmp_path: Path):
    database = DatabaseHandle(tmp_path / "cache.sqlite3", MessageCodec())
    yield WebhookHandles(database)
    database.close()


async def test_defers_only_unanswered_interactions():
    unanswered, answered = fake_interaction(done=False), fake_interaction(done=True)

    await defer_if_needed(unanswered)
    await defer_if_needed(answered)

    unanswered.response.defer.assert_awaited_once()
    answered.response.defer.assert_not_awaited()


async def test_deferring_an_interaction_answered_meanwhile_is_logged(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO)
    interaction = fake_interaction()
    interaction.response.defer.side_effect = discord.InteractionResponded(interaction)

    await defer_if_needed(interaction)

    assert "Could not defer the interaction" in caplog.text
    assert "test_deferring_an_interaction_answered_meanwhile_is_logged" in caplog.text  # the caller's stack


async def test_edit_prefers_the_cached_handle():
    handle_edit, partial_edit = AsyncMock(), AsyncMock()
    interaction = fake_interaction(channel=FakeChannel(partial_edit))

    assert await edit_message(interaction, ROLL_MESSAGE, handles_with(handle_edit), view="new view")

    handle_edit.assert_awaited_once_with(view="new view")
    partial_edit.assert_not_awaited()


@pytest.mark.parametrize("handles", ["missing", "failing"])
async def test_edit_falls_back_to_a_partial_message(handles: str):
    partial_edit = AsyncMock()
    interaction = fake_interaction(channel=FakeChannel(partial_edit))
    message_handles = MessageHandles() if handles == "missing" else handles_with(AsyncMock(side_effect=http_error()))

    assert await edit_message(interaction, ROLL_MESSAGE, message_handles, view="new view")

    partial_edit.assert_awaited_once_with(view="new view")


async def test_edit_reports_failure_when_no_way_works(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO)
    failing = fake_interaction(channel=FakeChannel(AsyncMock(side_effect=http_error())))
    unreachable = fake_interaction(channel=None)  # e.g. a group DM the bot isn't in

    assert not await edit_message(failing, ROLL_MESSAGE, MessageHandles(), view="new view")
    assert not await edit_message(unreachable, ROLL_MESSAGE, MessageHandles(), view="new view")
    assert "Editing via partial message 300 failed" in caplog.text
    assert "Invalid Webhook Token" in caplog.text  # an unexpected failure keeps its traceback


async def test_deleted_message_is_logged_without_a_traceback(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO)
    deleted = discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), {"code": 10008, "message": "Unknown"})
    interaction = fake_interaction(channel=FakeChannel(AsyncMock(side_effect=deleted)))

    assert not await edit_message(interaction, ROLL_MESSAGE, handles_with(AsyncMock(side_effect=deleted)), view="v")
    assert caplog.text.count("300 failed: the message no longer exists") == 2
    assert "Traceback" not in caplog.text


async def test_followup_remembers_the_message_it_was_opened_from(webhook_handles: WebhookHandles):
    interaction = fake_interaction(message_id=ROLL_MESSAGE)

    message = await send_ephemeral_followup(interaction, webhook_handles, view="menu")

    interaction.followup.send.assert_awaited_once_with(wait=True, ephemeral=True, view="menu")
    handle = webhook_handles.get(message.id)
    assert handle is not None
    assert (handle.message_id, handle.webhook_id, handle.original_target) == (500, 77, ROLL_MESSAGE)
    assert handle.expires_at - epoch_seconds() in range(FOLLOWUP_LIFETIME_SECONDS - 1, FOLLOWUP_LIFETIME_SECONDS + 1)


def test_expired_webhook_handle_reads_as_missing(webhook_handles: WebhookHandles):
    webhook_handles.remember(WebhookHandle(500, 77, expires_at=epoch_seconds() - 1, original_target=ROLL_MESSAGE))

    assert webhook_handles.get(500) is None


async def test_update_menu_edits_the_interactions_message():
    interaction, without_message = fake_interaction(message_id=500), fake_interaction()

    assert await update_menu(interaction, view="menu")
    assert not await update_menu(without_message, view="menu")

    interaction.followup.edit_message.assert_awaited_once_with(500, view="menu")
    without_message.followup.edit_message.assert_not_awaited()
