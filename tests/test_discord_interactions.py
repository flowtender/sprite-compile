from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from sprite_compile.codec import MessageCodec
from sprite_compile.discord.interactions import (
    TOKEN_LIFETIME_SECONDS,
    MenuOrigin,
    MessageHandles,
    defer_if_needed,
    edit_message,
    send_ephemeral_followup,
    update_menu,
)
from sprite_compile.storage import DatabaseHandle

ROLL_MESSAGE = 300
MENU_MESSAGE = 500
APPLICATION_ID = 9
USER_ID = 42
CLIENT = SimpleNamespace(application_id=APPLICATION_ID)


class FakeChannel:
    """A channel the bot can reach with its own token."""

    def __init__(self, edit: AsyncMock) -> None:
        self.partial = SimpleNamespace(edit=edit)

    def get_partial_message(self, message_id: int, /) -> SimpleNamespace:
        assert message_id == ROLL_MESSAGE
        return self.partial


class FakeWebhooks:
    """Stands in for `discord.Webhook.partial`: every interaction webhook shares these mocks."""

    def __init__(self) -> None:
        self.edited = SimpleNamespace(delete=AsyncMock())
        self.edit_message = AsyncMock(return_value=self.edited)
        self.delete_message = AsyncMock()
        self.used: list[tuple[int, str]] = []

    def partial(self, id: int, token: str, *, client: object) -> SimpleNamespace:
        self.used.append((id, token))
        return SimpleNamespace(edit_message=self.edit_message, delete_message=self.delete_message)


def fake_interaction(
    *,
    done: bool = False,
    channel: object = None,
    message_id: int | None = None,
    token: str = "token",
    age: timedelta = timedelta(0),
) -> Mock:
    interaction = Mock()
    interaction.client = CLIENT
    interaction.user = SimpleNamespace(id=USER_ID)
    interaction.token = token
    interaction.created_at = datetime.now(UTC) - age
    interaction.response.is_done.return_value = done
    interaction.response.defer = AsyncMock()
    interaction.channel = channel
    interaction.message = SimpleNamespace(id=message_id) if message_id is not None else None
    interaction.followup.send = AsyncMock(return_value=SimpleNamespace(id=MENU_MESSAGE, delete=AsyncMock()))
    interaction.followup.edit_message = AsyncMock()
    return interaction


def http_error() -> discord.HTTPException:
    return discord.HTTPException(SimpleNamespace(status=401, reason="Unauthorized"), "Invalid Webhook Token")


@pytest.fixture
def database(tmp_path: Path) -> Iterator[DatabaseHandle]:
    database = DatabaseHandle(tmp_path / "cache.sqlite3", MessageCodec())
    yield database
    database.close()


@pytest.fixture
def handles(database: DatabaseHandle) -> MessageHandles:
    return MessageHandles(database)


@pytest.fixture
def webhooks(monkeypatch: pytest.MonkeyPatch) -> FakeWebhooks:
    fake = FakeWebhooks()
    monkeypatch.setattr(discord.Webhook, "partial", fake.partial)
    return fake


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


async def test_edit_prefers_the_stored_token(handles: MessageHandles, webhooks: FakeWebhooks):
    partial_edit = AsyncMock()
    handles.remember(fake_interaction(token="roll token"), ROLL_MESSAGE)

    edited = await edit_message(fake_interaction(channel=FakeChannel(partial_edit)), ROLL_MESSAGE, handles, view="v")

    assert edited
    assert webhooks.used == [(APPLICATION_ID, "roll token")]
    webhooks.edit_message.assert_awaited_once_with(ROLL_MESSAGE, view="v")
    partial_edit.assert_not_awaited()


@pytest.mark.parametrize("token", ["missing", "failing"])
async def test_edit_falls_back_to_a_partial_message(token: str, handles: MessageHandles, webhooks: FakeWebhooks):
    partial_edit = AsyncMock()
    if token == "failing":
        handles.remember(fake_interaction(), ROLL_MESSAGE)
        webhooks.edit_message.side_effect = http_error()

    assert await edit_message(fake_interaction(channel=FakeChannel(partial_edit)), ROLL_MESSAGE, handles, view="v")

    partial_edit.assert_awaited_once_with(view="v")


async def test_edit_reports_failure_when_no_way_works(handles: MessageHandles, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO)
    failing = fake_interaction(channel=FakeChannel(AsyncMock(side_effect=http_error())))
    unreachable = fake_interaction(channel=None)  # e.g. a group DM the bot isn't in

    assert not await edit_message(failing, ROLL_MESSAGE, handles, view="v")
    assert not await edit_message(unreachable, ROLL_MESSAGE, handles, view="v")
    assert "Editing via partial message 300 failed" in caplog.text
    assert "Invalid Webhook Token" in caplog.text  # an unexpected failure keeps its traceback


async def test_deleted_message_is_logged_without_a_traceback(
    handles: MessageHandles, webhooks: FakeWebhooks, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO)
    deleted = discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), {"code": 10008, "message": "Unknown"})
    webhooks.edit_message.side_effect = deleted
    handles.remember(fake_interaction(), ROLL_MESSAGE)
    interaction = fake_interaction(channel=FakeChannel(AsyncMock(side_effect=deleted)))

    assert not await edit_message(interaction, ROLL_MESSAGE, handles, view="v")
    assert caplog.text.count("300 failed: the message no longer exists") == 2
    assert "Traceback" not in caplog.text


def test_token_expires_15_minutes_after_its_interaction(handles: MessageHandles):
    handles.remember(fake_interaction(age=timedelta(seconds=TOKEN_LIFETIME_SECONDS)), ROLL_MESSAGE)
    handles.remember(fake_interaction(age=timedelta(seconds=TOKEN_LIFETIME_SECONDS - 60)), MENU_MESSAGE)

    assert handles.get(ROLL_MESSAGE) is None
    assert handles.get(MENU_MESSAGE) is not None


def test_tokens_survive_a_restart(database: DatabaseHandle):
    MessageHandles(database).remember(fake_interaction(token="roll token"), ROLL_MESSAGE)

    handle = MessageHandles(database).get(ROLL_MESSAGE)

    assert handle is not None and handle.token == "roll token"


async def test_followup_deletes_itself_when_its_token_expires(handles: MessageHandles):
    interaction = fake_interaction(age=timedelta(minutes=5))

    message = await send_ephemeral_followup(interaction, handles, view="menu")

    interaction.followup.send.assert_awaited_once_with(wait=True, ephemeral=True, view="menu")
    (delay,) = message.delete.await_args.kwargs.values()
    assert abs(delay - (TOKEN_LIFETIME_SECONDS - 5 * 60)) <= 1


async def test_leftover_menus_are_rebuilt_at_startup(handles: MessageHandles, webhooks: FakeWebhooks):
    opened = fake_interaction(message_id=ROLL_MESSAGE, token="menu token", age=timedelta(minutes=5))
    await send_ephemeral_followup(opened, handles, view="menu")
    handles.remember(fake_interaction(), ROLL_MESSAGE)
    origins: list[MenuOrigin] = []

    def build_menu(origin: MenuOrigin) -> str:
        origins.append(origin)
        return "rebuilt menu"

    await handles.restore_menus(CLIENT, build_menu)  # type: ignore[arg-type]

    assert origins == [MenuOrigin(ROLL_MESSAGE, USER_ID)]
    assert webhooks.used == [(APPLICATION_ID, "menu token")]
    webhooks.edit_message.assert_awaited_once_with(MENU_MESSAGE, view="rebuilt menu")
    (delay,) = webhooks.edited.delete.await_args.kwargs.values()
    assert abs(delay - (TOKEN_LIFETIME_SECONDS - 5 * 60)) <= 1


async def test_leftover_menu_without_a_view_is_deleted(handles: MessageHandles, webhooks: FakeWebhooks):
    await send_ephemeral_followup(fake_interaction(message_id=ROLL_MESSAGE), handles, view="menu")

    await handles.restore_menus(CLIENT, lambda origin: None)  # type: ignore[arg-type]

    webhooks.delete_message.assert_awaited_once_with(MENU_MESSAGE)
    assert handles.get(MENU_MESSAGE) is None


async def test_update_menu_edits_the_interactions_message():
    interaction, without_message = fake_interaction(message_id=500), fake_interaction()

    assert await update_menu(interaction, view="menu")
    assert not await update_menu(without_message, view="menu")

    interaction.followup.edit_message.assert_awaited_once_with(500, view="menu")
    without_message.followup.edit_message.assert_not_awaited()
