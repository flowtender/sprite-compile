"""Dice rolls.

More about dice."""

from __future__ import annotations

from typing import Annotated

from discord.app_commands import Choice, Range

from sprite_compile.discord.commands import Choices, Desc, command

MODES = (Choice(name="Fast", value="fast"), Choice(name="Careful", value="careful"))


@command()
def roll_pool(
    *,
    dice: Annotated[Range[int, 1, 99], Desc("Number of dice.")],
    mode: Annotated[str, Desc("How to roll."), Choices(MODES)] = "fast",
    limit: Annotated[int | None, Desc("Hit limit.")] = None,
) -> tuple[int, str, int | None]:
    """Roll a dice pool.

    Not part of the description."""
    return dice, mode, limit


@command(name="explicit", desc="Named by the decorator.")
def roll_named() -> None:
    pass
