"""`ContainerView`: the base for a sprite's messages, a components-v2 `LayoutView` holding one `ui.Container`, with
helpers that add text, separators, sections and buttons to it. Buttons fill action rows of five; anything else added
after a button starts the next button on a new row."""

from __future__ import annotations

from discord import File, UnfurledMediaItem, ui

BUTTONS_PER_ROW = 5


class ContainerView(ui.LayoutView):
    def __init__(self, *, accent_color: int | None = None, timeout: float | None = None) -> None:
        super().__init__(timeout=timeout)
        self.container: ui.Container = ui.Container(accent_color=accent_color)
        self.add_item(self.container)
        self.button_row: ui.ActionRow | None = None

    def add_to_container(self, item: ui.Item) -> None:
        self.container.add_item(item)
        self.button_row = None

    def add_text(self, text: str) -> None:
        self.add_to_container(ui.TextDisplay(text))

    def add_separator(self) -> None:
        self.add_to_container(ui.Separator())

    def add_section(self, text: str, thumbnail: str | File | UnfurledMediaItem) -> None:
        self.add_to_container(ui.Section(ui.TextDisplay(text), accessory=ui.Thumbnail(thumbnail)))

    def add_button(self, button: ui.Item) -> None:
        """Add a button (or a `PersistentButton`) to the last row of buttons, or a new row when that one is full."""
        if self.button_row is None or len(self.button_row.children) >= BUTTONS_PER_ROW:
            row = ui.ActionRow()
            self.add_to_container(row)
            self.button_row = row
        self.button_row.add_item(button)
