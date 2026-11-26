from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from button_fixtures.buttons import Edge, EdgeButton, MenuButton, RollButton
from sprite_compile.discord.persistent import PERSISTENT_BUTTONS, PersistentButton


async def clicked(button: PersistentButton) -> PersistentButton:
    """The button discord.py rebuilds when the one that was sent is clicked."""
    sent = button.item
    match = type(button).__discord_ui_compiled_template__.match(sent.custom_id)
    assert match is not None
    return await type(button).from_custom_id(SimpleNamespace(), sent, match)  # type: ignore[arg-type]


async def test_a_click_rebuilds_the_buttons_state_and_look():
    button = EdgeButton(
        record_id=1234,
        edge=Edge.SECOND_CHANCE,
        pre_edge=True,
        note="x y",
        label="2nd",
        style=discord.ButtonStyle.danger,
    )
    assert button.custom_id == "t:edge:1234:SECOND_CHANCE:1:x y"

    rebuilt = await clicked(button)
    assert isinstance(rebuilt, EdgeButton)
    assert (rebuilt.record_id, rebuilt.edge, rebuilt.pre_edge, rebuilt.note) == (1234, Edge.SECOND_CHANCE, True, "x y")
    assert (rebuilt.item.label, rebuilt.item.style) == ("2nd", discord.ButtonStyle.danger)


def test_custom_id_over_discords_limit_is_rejected():
    with pytest.raises(ValueError, match=r"EdgeButton: custom_id .* is 101 characters, Discord allows 100"):
        EdgeButton(record_id=1, edge=Edge.PUSH, pre_edge=False, note="n" * 85)


def test_string_field_with_a_colon_is_rejected():
    with pytest.raises(ValueError, match="can't contain ':'"):
        EdgeButton(record_id=1, edge=Edge.PUSH, pre_edge=False, note="a:b")


def test_missing_field_is_rejected():
    with pytest.raises(TypeError, match=r"MenuButton takes the fields \['record_id'\], got \[\]"):
        MenuButton()


def test_base_without_prefix_cant_be_built():
    with pytest.raises(TypeError, match="RollButton has no prefix"):
        RollButton(record_id=1)


def test_unsupported_field_type_fails_at_class_creation():
    with pytest.raises(TypeError, match=r"Floaty.ratio: a persistent button field must be int, bool, str or an enum"):

        class Floaty(PersistentButton, prefix="t:float"):
            ratio: float


def fake_interaction(user_id: int) -> discord.Interaction:
    response = SimpleNamespace(send_message=AsyncMock())
    return SimpleNamespace(user=SimpleNamespace(id=user_id), response=response)  # type: ignore[return-value]


async def test_refused_click_gets_the_refusal():
    interaction = fake_interaction(user_id=8)
    assert not await MenuButton(record_id=1).interaction_check(interaction)
    interaction.response.send_message.assert_awaited_once_with(
        "You are not a participant in that roll.", ephemeral=True
    )


async def test_allowed_click_goes_through_silently():
    interaction = fake_interaction(user_id=7)
    assert await MenuButton(record_id=1).interaction_check(interaction)
    interaction.response.send_message.assert_not_awaited()


def test_buttons_register_on_definition_but_bases_dont():
    assert PERSISTENT_BUTTONS["t:edge"] is EdgeButton
    assert RollButton not in PERSISTENT_BUTTONS.values()


def test_duplicate_prefix_names_both_classes():
    with pytest.raises(TypeError, match=r"Copycat: the prefix 't:edge' is already taken by .*buttons\.EdgeButton"):

        class Copycat(PersistentButton, prefix="t:edge"):
            pass

    assert PERSISTENT_BUTTONS["t:edge"] is EdgeButton
