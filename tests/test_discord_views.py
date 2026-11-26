from __future__ import annotations

from discord import ui

from sprite_compile.discord.views import ContainerView


def layout(view: ContainerView) -> list[str | int]:
    """The container's children: "text" for text, the button count for an action row."""
    return [len(child.children) if isinstance(child, ui.ActionRow) else "text" for child in view.container.children]


def test_buttons_fill_rows_of_five_and_text_closes_a_row():
    view = ContainerView()
    for _ in range(7):
        view.add_button(ui.Button(label="b"))
    view.add_text("between")
    view.add_button(ui.Button(label="b"))
    assert layout(view) == [5, 2, "text", 1]
