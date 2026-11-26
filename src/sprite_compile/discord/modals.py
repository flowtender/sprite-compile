"""Modals built from validated fields, from chance's `BuiltModal`.

Each field is a `ui.Label` whose `validate()` returns its typed value or raises `ValueError` with a message for the
user. The modal's own options come first; every other keyword argument is a field, named for the submit callback:

    BuiltModal(
        "Resistance roll",
        body=f"Rolling to resist {record.label}",
        on_submit=resist,
        # fields
        dice=NumberField("Number of dice", 0, 99),
        pre_edge=CheckboxField("Pre-edge? (add your edge above, if so)"),
    )

    async def resist(interaction: discord.Interaction, *, dice: int, pre_edge: bool) -> None: ...
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Sequence
from typing import Any

import discord
from discord import ui

SubmitCallback = Callable[..., Awaitable[None]]


class ValidLabel[T](ui.Label):
    def validate(self) -> T:
        raise NotImplementedError


class NumberField(ValidLabel[int]):
    """A whole number between `min_value` and `max_value`; an optional field left empty gives `empty_value`."""

    def __init__(
        self,
        label: str,
        min_value: int,
        max_value: int,
        *,
        description: str | None = None,
        placeholder: str | None = None,
        default: int | None = None,
        required: bool = True,
        empty_value: int = 0,
    ) -> None:
        self.input = ui.TextInput(
            placeholder=placeholder,
            default=None if default is None else str(default),
            required=required,
            min_length=1,
            max_length=max(len(f"{min_value:+}"), len(f"{max_value:+}")),
        )
        super().__init__(text=label, component=self.input, description=description)
        self.min_value = min_value
        self.max_value = max_value
        self.empty_value = empty_value

    def validate(self) -> int:
        raw = self.input.value.strip()
        if not raw and not self.input.required:
            return self.empty_value
        try:
            value = int(raw)
        except ValueError:
            value = None
        if value is None or not self.min_value <= value <= self.max_value:
            raise ValueError(f"{self.text}: enter a whole number between {self.min_value} and {self.max_value}.")
        return value


class CheckboxField(ValidLabel[bool]):
    def __init__(self, label: str, *, description: str | None = None, default: bool = False) -> None:
        self.input = ui.Checkbox(default=default)
        super().__init__(text=label, component=self.input, description=description)

    def validate(self) -> bool:
        return self.input.value


class RadioField[T](ValidLabel[T | None]):
    """One of `options` (label, value) pairs; an optional field left empty gives None."""

    def __init__(
        self,
        label: str,
        options: Sequence[tuple[str, T]],
        *,
        description: str | None = None,
        default: T | None = None,
        required: bool = True,
    ) -> None:
        self.option_values = [value for _, value in options]
        self.input = ui.RadioGroup(
            required=required,
            options=[
                discord.RadioGroupOption(label=text, value=str(index), default=value == default)
                for index, (text, value) in enumerate(options)
            ],
        )
        super().__init__(text=label, component=self.input, description=description)

    def validate(self) -> T | None:
        if self.input.value is None:
            if self.input.required:
                raise ValueError(f"{self.text}: pick one.")
            return None
        return self.option_values[int(self.input.value)]


class CheckboxGroupField[T](ValidLabel[list[T]]):
    """The ticked `options` (label, value) pairs' values, in option order."""

    def __init__(
        self,
        label: str,
        options: Sequence[tuple[str, T]],
        *,
        description: str | None = None,
        defaults: Collection[T] = (),
        min_values: int | None = None,
        max_values: int | None = None,
        required: bool = True,
    ) -> None:
        self.option_values = [value for _, value in options]
        self.input = ui.CheckboxGroup(
            required=required,
            min_values=min_values,
            max_values=max_values,
            options=[
                discord.CheckboxGroupOption(label=text, value=str(index), default=value in defaults)
                for index, (text, value) in enumerate(options)
            ],
        )
        super().__init__(text=label, component=self.input, description=description)

    def validate(self) -> list[T]:
        ticked = {int(raw) for raw in self.input.values}
        return [value for index, value in enumerate(self.option_values) if index in ticked]


class BuiltModal(ui.Modal):
    """A modal of `fields`, with an optional `body` text above them. On submit every field validates; the first
    `ValueError` is shown to the user (ephemeral, gone after `delete_after` seconds), else `on_submit` gets the values
    as keyword arguments named like the fields."""

    def __init__(
        self,
        title: str,
        *,
        body: str | None = None,
        on_submit: SubmitCallback,
        delete_after: float | None = 5,
        **fields: ValidLabel[Any],
    ) -> None:
        super().__init__(title=title, timeout=None)
        self.submit_callback = on_submit
        self.delete_after = delete_after
        self.fields = fields
        if body is not None:
            self.add_item(ui.TextDisplay(body))
        for field in fields.values():
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            values = {name: field.validate() for name, field in self.fields.items()}
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True, delete_after=self.delete_after)
            return
        await self.submit_callback(interaction, **values)
