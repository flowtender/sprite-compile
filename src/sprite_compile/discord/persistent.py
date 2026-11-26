"""Buttons that survive restarts: `DynamicItem`s whose `custom_id` carries their state, generated from typed fields.

    class AdjustDice(PersistentButton, prefix="cs:dice"):
        record_id: int

        async def callback(self, interaction: discord.Interaction) -> None: ...

    view.add_button(AdjustDice(record_id=record.id, label="Adjust dice"))

The `custom_id` is the prefix and the field values joined by `:` (`cs:dice:1234`), at most 100 characters (Discord's
limit, checked when the button is built). Fields are identifiers and short keys (a record ID, an action); the data
itself stays in the database. They are `int`, `bool`, `str` (without `:`) or enums (by member name), in declaration
order, base classes' fields first. A click rebuilds the button from its `custom_id`, keeping the label, emoji and
style it was sent with.

Every class with a prefix registers itself when it's defined (a second class with the same prefix is an error), and
`DiscordSprite` hands them all to discord.py, so a button's module must be imported by the time the bot logs in (e.g.
by an extension). A class without a prefix is a base for others and is never registered.

`allowed()` gates every click; a refused click gets `refusal` as an ephemeral reply. Changing a prefix or the fields
breaks the buttons on every message already posted.

Don't put persistent buttons in a view that also holds ordinary buttons and gets stopped or times out: discord.py
(2.7) then unregisters the persistent button's class for every message until the next login.
"""

from __future__ import annotations

import inspect
import re
from enum import Enum
from typing import Any, ClassVar, get_origin

import discord
from discord import ui

CUSTOM_ID_LIMIT = 100
NEVER_MATCHES = r"(?!)"

FIELD_PATTERNS: dict[type, str] = {int: r"-?\d+", bool: r"[01]", str: r"[^:]*"}


def field_pattern(field_type: type) -> str:
    if issubclass(field_type, Enum):
        return r"\w+"
    return FIELD_PATTERNS[field_type]


def encode_field(value: Any) -> str:
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def decode_field(raw: str, field_type: type) -> Any:
    if issubclass(field_type, Enum):
        return field_type[raw]
    if field_type is bool:
        return raw == "1"
    return field_type(raw)


def declared_fields(cls: type) -> dict[str, type]:
    """The state fields annotated on `cls` and its `PersistentButton` bases, base classes first."""
    fields: dict[str, type] = {}
    for klass in reversed(cls.__mro__):
        if not issubclass(klass, PersistentButton) or klass is PersistentButton:
            continue
        for name, annotation in inspect.get_annotations(klass, eval_str=True).items():
            if get_origin(annotation) is ClassVar or annotation is ClassVar:
                continue
            if not (isinstance(annotation, type) and (annotation in FIELD_PATTERNS or issubclass(annotation, Enum))):
                raise TypeError(
                    f"{cls.__qualname__}.{name}: a persistent button field must be int, bool, str or an enum"
                )
            fields[name] = annotation
    return fields


# Every persistent button class with a prefix, by prefix; DiscordSprite registers them with discord.py
PERSISTENT_BUTTONS: dict[str, type[PersistentButton]] = {}


def register(cls: type[PersistentButton]) -> None:
    assert cls.prefix is not None
    if (taken_by := PERSISTENT_BUTTONS.get(cls.prefix)) is not None:
        raise TypeError(
            f"{cls.__module__}.{cls.__qualname__}: the prefix {cls.prefix!r} is already taken by "
            f"{taken_by.__module__}.{taken_by.__qualname__}"
        )
    PERSISTENT_BUTTONS[cls.prefix] = cls


class PersistentButton(ui.DynamicItem[ui.Button], template=NEVER_MATCHES):
    prefix: ClassVar[str | None] = None
    fields: ClassVar[dict[str, type]] = {}
    refusal: ClassVar[str] = "You can't use this button."

    def __init_subclass__(cls, *, prefix: str | None = None, **kwargs: Any) -> None:
        cls.prefix = prefix
        cls.fields = declared_fields(cls)
        if prefix is None:
            template = NEVER_MATCHES
        else:
            template = re.escape(prefix) + "".join(
                f":(?P<{name}>{field_pattern(field_type)})" for name, field_type in cls.fields.items()
            ) + "$"
        super().__init_subclass__(template=template, **kwargs)
        if prefix is not None:
            register(cls)

    def __init__(
        self,
        *,
        label: str | None = None,
        emoji: str | discord.Emoji | discord.PartialEmoji | None = None,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
        **state: Any,
    ) -> None:
        cls = type(self)
        if cls.prefix is None:
            raise TypeError(f"{cls.__qualname__} has no prefix; it's a base for persistent buttons")
        if state.keys() != cls.fields.keys():
            raise TypeError(f"{cls.__qualname__} takes the fields {list(cls.fields)}, got {list(state)}")
        encoded = [encode_field(state[name]) for name in cls.fields]
        if any(":" in value for value in encoded):
            raise ValueError(f"{cls.__qualname__}: field values can't contain ':', got {encoded}")
        custom_id = ":".join([cls.prefix, *encoded])
        if len(custom_id) > CUSTOM_ID_LIMIT:
            raise ValueError(
                f"{cls.__qualname__}: custom_id {custom_id!r} is {len(custom_id)} characters, Discord allows "
                f"{CUSTOM_ID_LIMIT}"
            )
        for name, value in state.items():
            setattr(self, name, value)
        super().__init__(ui.Button(label=label, emoji=emoji, style=style, disabled=disabled, custom_id=custom_id))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Item[Any], match: re.Match[str], /):
        button = item if isinstance(item, ui.Button) else ui.Button()
        state = {name: decode_field(match[name], field_type) for name, field_type in cls.fields.items()}
        return cls(label=button.label, emoji=button.emoji, style=button.style, disabled=button.disabled, **state)

    async def allowed(self, interaction: discord.Interaction) -> bool:
        return True

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if await self.allowed(interaction):
            return True
        await interaction.response.send_message(self.refusal, ephemeral=True)
        return False
