from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest
from discord import ui

from sprite_compile.discord.modals import (
    BuiltModal,
    CheckboxField,
    CheckboxGroupField,
    NumberField,
    RadioField,
    ValidLabel,
)

INTERACTION: Any = SimpleNamespace()
SIZES = [("Small", "s"), ("Medium", "m"), ("Large", "l")]


def fill(field: ValidLabel[Any], **data: Any) -> None:
    """What discord.py does with a modal submission's `data` (e.g. `value=` or `values=`) for the field's input."""
    field.input._handle_submit(INTERACTION, data, {})  # type: ignore[attr-defined]


def submitted[T](field: ValidLabel[T], **data: Any) -> T:
    fill(field, **data)
    return field.validate()


def test_number_in_range_is_returned():
    assert submitted(NumberField("Dice", 0, 99), value=" 12 ") == 12


@pytest.mark.parametrize("raw", ["100", "-1", "abc", "1.5"])
def test_bad_number_names_the_field_and_range(raw: str):
    with pytest.raises(ValueError, match=r"^Dice: enter a whole number between 0 and 99\.$"):
        submitted(NumberField("Dice", 0, 99), value=raw)


def test_empty_optional_number_gives_empty_value():
    assert submitted(NumberField("Limit", 0, 99, required=False, empty_value=-1), value="") == -1


def test_checkbox_gives_a_bool():
    assert submitted(CheckboxField("Pre-edge?"), value=True) is True


def test_radio_gives_the_chosen_value_not_the_wire_string():
    assert submitted(RadioField("Size", SIZES), value="1") == "m"


def test_required_radio_left_empty_asks_to_pick():
    with pytest.raises(ValueError, match="Size: pick one."):
        submitted(RadioField("Size", SIZES), value=None)


def test_optional_radio_left_empty_gives_none():
    assert submitted(RadioField("Size", SIZES, required=False), value=None) is None


def test_radio_default_is_preselected():
    field = RadioField("Size", SIZES, default="l")
    assert [option.default for option in field.input.options] == [False, False, True]


def test_checkbox_group_gives_ticked_values_in_option_order():
    field = CheckboxGroupField("Sizes", SIZES, defaults={"s"})
    assert [option.default for option in field.input.options] == [True, False, False]
    assert submitted(field, values=["2", "0"]) == ["s", "l"]


def response_interaction() -> Any:
    return SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))


def resist_modal(on_submit: AsyncMock) -> BuiltModal:
    return BuiltModal(
        "Resistance roll",
        body="Rolling to resist",
        on_submit=on_submit,
        # fields
        dice=NumberField("Number of dice", 0, 99),
        pre_edge=CheckboxField("Pre-edge?"),
    )


def test_body_comes_before_the_fields():
    children = resist_modal(AsyncMock()).children
    assert isinstance(children[0], ui.TextDisplay)
    assert [child.text for child in children[1:]] == ["Number of dice", "Pre-edge?"]  # type: ignore[attr-defined]


async def test_submit_gets_the_values_by_field_name():
    on_submit = AsyncMock()
    modal = resist_modal(on_submit)
    fill(modal.fields["dice"], value="4")
    fill(modal.fields["pre_edge"], value=True)
    interaction = response_interaction()

    await modal.on_submit(interaction)

    on_submit.assert_awaited_once_with(interaction, dice=4, pre_edge=True)


async def test_invalid_submit_shows_the_error_briefly_and_skips_the_callback():
    on_submit = AsyncMock()
    modal = resist_modal(on_submit)
    fill(modal.fields["dice"], value="many")
    interaction = response_interaction()

    await modal.on_submit(interaction)

    on_submit.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once_with(
        "Number of dice: enter a whole number between 0 and 99.", ephemeral=True, delete_after=5
    )
