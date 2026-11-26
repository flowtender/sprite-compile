from __future__ import annotations

from enum import Enum
from typing import ClassVar

import discord

from sprite_compile.discord.persistent import PersistentButton


class Edge(Enum):
    PUSH = 1
    SECOND_CHANCE = 2


class RollButton(PersistentButton):
    """A base for buttons on a roll: no prefix, never registered."""

    record_id: int
    refusal: ClassVar[str] = "You are not a participant in that roll."

    async def allowed(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == 7


class EdgeButton(RollButton, prefix="t:edge"):
    edge: Edge
    pre_edge: bool
    note: str


class MenuButton(RollButton, prefix="t:menu"):
    pass
