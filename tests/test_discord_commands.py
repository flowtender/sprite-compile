from __future__ import annotations

from typing import Annotated, Any
from unittest.mock import AsyncMock

import discord
import pytest
from discord import app_commands

import command_fixtures
from command_fixtures.dice import roll_pool
from sprite_compile.discord.commands import Desc, build_command, build_command_group, command


def children(group: app_commands.Group) -> dict[str, Any]:
    return {
        child.name: children(child) if isinstance(child, app_commands.Group) else child.description
        for child in group.commands
    }


def build_fixture_group(**options: Any) -> app_commands.Group:
    return build_command_group("bot", "The bot's commands.", AsyncMock(), package=command_fixtures, **options)


def test_package_commands_go_into_subgroups_named_after_their_modules():
    group = build_fixture_group(strip_prefix="roll_")
    assert children(group) == {
        "dice": {
            "pool": "Roll a dice pool.",
            "explicit": "Named by the decorator.",
            "moved": "Moved into dice by the decorator.",
        },
        "misc": {"coin": "Undocumented module."},
    }


def test_subgroups_are_described_by_their_module_docstring_or_name():
    descriptions = {child.name: child.description for child in build_fixture_group().commands}
    assert descriptions == {"dice": "Dice rolls.", "misc": "Misc commands"}


def test_parameters_carry_descriptions_ranges_choices_and_defaults():
    parameters = {p.name: p for p in build_command(roll_pool, AsyncMock()).parameters}

    assert parameters["dice"].description == "Number of dice."
    assert parameters["dice"].required
    assert (parameters["dice"].min_value, parameters["dice"].max_value) == (1, 99)

    assert [choice.value for choice in parameters["mode"].choices] == ["fast", "careful"]
    assert parameters["mode"].default == "fast"

    assert parameters["limit"].type is discord.AppCommandOptionType.integer
    assert not parameters["limit"].required
    assert parameters["limit"].default is None


def test_group_serializes_for_sync():
    group = build_fixture_group()
    tree = app_commands.CommandTree(discord.Client(intents=discord.Intents.none()))
    tree.add_command(group)
    payload = group.to_dict(tree)
    dice = next(option for option in payload["options"] if option["name"] == "dice")
    pool = next(option for option in dice["options"] if option["name"] == "roll_pool")
    assert [option["name"] for option in pool["options"]] == ["dice", "mode", "limit"]


async def test_invoke_gets_the_function_and_its_arguments():
    invoke = AsyncMock()
    interaction = object()
    await build_command(roll_pool, invoke).callback(interaction, dice=3, mode="careful", limit=None)
    invoke.assert_awaited_once_with(interaction, roll_pool, {"dice": 3, "mode": "careful", "limit": None})


LABEL_OPTION = {"label": Annotated[str, Desc("What the roll is for.")]}


async def test_leading_options_come_first_required_and_reach_invoke():
    invoke = AsyncMock()
    group = build_fixture_group(leading_options=LABEL_OPTION)
    pool = next(c for c in group.walk_commands() if isinstance(c, app_commands.Command) and c.name == "roll_pool")
    assert [(p.name, p.required) for p in pool.parameters] == [
        ("label", True),
        ("dice", True),
        ("mode", False),
        ("limit", False),
    ]
    assert pool.get_parameter("label").description == "What the roll is for."  # type: ignore[union-attr]

    command = build_command(roll_pool, invoke, leading_options=LABEL_OPTION)
    interaction = object()
    await command.callback(interaction, label="Sneaking", dice=3)  # type: ignore[call-arg]
    invoke.assert_awaited_once_with(interaction, roll_pool, {"label": "Sneaking", "dice": 3})


def test_leading_option_clashing_with_a_parameter_is_rejected():
    with pytest.raises(TypeError, match=r"roll_pool: parameters \['dice'\] clash"):
        build_command(roll_pool, AsyncMock(), leading_options={"dice": Annotated[int, Desc("Dice.")]})


def test_listed_functions_keep_their_name_and_go_to_the_top_level():
    def roll_flip() -> None:
        """Flip."""

    group = build_fixture_group(strip_prefix="roll_", functions=[roll_flip])
    assert children(group)["roll_flip"] == "Flip."


def test_installs_and_contexts_are_applied():
    group = build_command_group(
        "bot",
        "The bot's commands.",
        AsyncMock(),
        allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
        allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True),
    )
    assert group.allowed_installs is not None and group.allowed_installs.user
    assert group.allowed_contexts is not None and group.allowed_contexts.private_channel


def test_parameter_without_desc_is_rejected():
    def roll_bad(*, dice: int) -> None:
        """Bad."""

    with pytest.raises(TypeError, match=r"roll_bad: parameter 'dice' has no Desc"):
        build_command(roll_bad, AsyncMock())


def test_command_without_description_is_rejected():
    def roll_bare(*, dice: Annotated[int, Desc("Dice.")]) -> None:
        pass

    with pytest.raises(TypeError, match=r"roll_bare: no @command\(desc=...\) and no docstring"):
        build_command(roll_bare, AsyncMock())


def test_duplicate_command_names_the_function_that_got_there_first():
    @command(group="dice", name="pool", desc="Again.")
    def roll_again() -> None:
        pass

    with pytest.raises(ValueError, match=r"roll_again: /bot dice pool is already taken by .*dice\.roll_pool"):
        build_fixture_group(strip_prefix="roll_", functions=[roll_again])


def test_command_clashing_with_a_subgroup_is_rejected():
    @command(name="dice", desc="Clashes with the dice subgroup.")
    def roll_dice() -> None:
        pass

    with pytest.raises(ValueError, match=r"roll_dice: /bot dice is already taken by the 'dice' subgroup"):
        build_fixture_group(functions=[roll_dice])


def numbered_command(number: int):
    @command(name=f"c{number}", desc="Numbered.")
    def roll() -> None:
        pass

    return roll


def test_group_over_discords_limit_names_the_function_that_overflowed():
    functions = [numbered_command(number) for number in range(26)]
    with pytest.raises(ValueError, match=r"numbered_command.<locals>.roll: /bot already has 25 commands"):
        build_command_group("bot", "The bot's commands.", AsyncMock(), functions=functions)
