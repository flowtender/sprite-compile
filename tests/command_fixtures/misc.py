from __future__ import annotations

from sprite_compile.discord.commands import command
from command_fixtures.dice import roll_pool  # noqa: F401  (imported, so it stays in dice)


@command(desc="Undocumented module.")
def roll_coin() -> None:
    pass


@command(group="dice", desc="Moved into dice by the decorator.")
def roll_moved() -> None:
    pass
