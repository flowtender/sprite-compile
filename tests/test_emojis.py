from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from PIL import Image

from sprite_compile.emojis import sync_application_emojis


@dataclass
class FakeEmoji:
    name: str
    id: int

    def __str__(self) -> str:
        return f"<:{self.name}:{self.id}>"


def http_error(status: int, message: str) -> discord.HTTPException:
    return discord.HTTPException(SimpleNamespace(status=status, reason="error"), message)


def image_bytes(format: str) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA" if format != "JPEG" else "RGB", (8, 8)).save(buffer, format=format)
    return buffer.getvalue()


class FakeClient:
    """An application's emoji list on Discord's side. Names in `rejected` fail to upload; names in `lost_response`
    are stored but the client gets an error, as when a request times out after Discord processed it."""

    def __init__(self, *existing: str) -> None:
        self.server = {name: FakeEmoji(name, id) for id, name in enumerate(existing, start=1)}
        self.rejected: set[str] = set()
        self.lost_response: set[str] = set()
        self.fetch_application_emojis = AsyncMock(side_effect=lambda: list(self.server.values()))
        self.create_application_emoji = AsyncMock(side_effect=self.create)

    async def create(self, *, name: str, image: bytes) -> FakeEmoji:
        if name in self.rejected:
            raise http_error(400, "Invalid image")
        self.server[name] = FakeEmoji(name, 100 + len(self.server))
        if name in self.lost_response:
            raise http_error(504, "Gateway timeout")
        return self.server[name]

    def uploads(self) -> dict[str, bytes]:
        return {call.kwargs["name"]: call.kwargs["image"] for call in self.create_application_emoji.await_args_list}


@pytest.fixture
def assets(tmp_path: Path) -> Path:
    (tmp_path / "reroll.png").write_bytes(image_bytes("PNG"))
    (tmp_path / "d6r1.gif").write_bytes(image_bytes("PNG"))  # wrong extension; the content decides
    (tmp_path / "glitch.webp").write_bytes(image_bytes("WEBP"))
    (tmp_path / "__init__.py").write_text("")
    (tmp_path / "notes.png").write_text("not an image despite the name")
    (tmp_path / "nested.png").mkdir()
    return tmp_path


async def test_uploads_images_by_content_only_when_the_name_is_missing(assets: Path):
    client = FakeClient("reroll")

    emojis = await sync_application_emojis(client, assets)

    assert client.uploads() == {"d6r1": image_bytes("PNG"), "glitch": image_bytes("WEBP")}
    assert emojis.markup == {"reroll": "<:reroll:1>", "d6r1": "<:d6r1:101>", "glitch": "<:glitch:102>"}


async def test_nothing_to_upload_fetches_once(assets: Path):
    client = FakeClient("reroll", "d6r1", "glitch")

    await sync_application_emojis(client, assets)

    client.create_application_emoji.assert_not_awaited()
    client.fetch_application_emojis.assert_awaited_once()


async def test_corrupted_image_is_skipped_with_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    corrupted = bytearray(image_bytes("PNG"))
    corrupted[-20] ^= 0xFF  # inside the pixel data, so the PNG opens but fails its checksum
    (tmp_path / "broken.png").write_bytes(corrupted)
    client = FakeClient()

    await sync_application_emojis(client, tmp_path)

    client.create_application_emoji.assert_not_awaited()
    assert "broken.png" in caplog.text


async def test_failed_upload_falls_back_and_the_server_has_the_last_word(assets: Path, caplog: pytest.LogCaptureFixture):
    client = FakeClient()
    client.rejected = {"d6r1"}
    client.lost_response = {"glitch"}

    emojis = await sync_application_emojis(client, assets)

    assert "Could not upload the emoji d6r1" in caplog.text
    assert emojis.get("d6r1", "①") == "①"
    assert emojis.get("glitch", "⚠") == "<:glitch:100>"


async def test_unreachable_emoji_list_uploads_nothing_and_falls_back(assets: Path):
    client = FakeClient()
    client.fetch_application_emojis.side_effect = http_error(503, "Service unavailable")

    emojis = await sync_application_emojis(client, assets)

    client.create_application_emoji.assert_not_awaited()
    assert emojis.get("reroll", "♻️") == "♻️"
