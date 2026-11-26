from __future__ import annotations

import socket
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import discord
import msgspec
import pytest
from discord.ext import commands

from sprite_compile.config import ConfigSection, Setting
from sprite_compile.discord_sprite import DiscordSettings, DiscordSprite
from sprite_compile.runner import run
from sprite_compile.secrets import Secret
from sprite_compile.systemd import notify_ready

SECRETS = {"discord_token": Secret("Bot token from the Developer Portal")}


class BotConfig(ConfigSection):
    discord: Annotated[DiscordSettings, Setting("Discord connection.", added="0.1.0")] = msgspec.field(
        default_factory=DiscordSettings
    )
    greeting: Annotated[str, Setting("What the bot says.", added="0.1.0")] = "hello"


class FakeSprite(DiscordSprite):
    """Records the state at the point it would log in to Discord, instead of logging in."""

    extension_names = ("discord_fixtures.ping",)

    def __init__(self, config: BotConfig, secrets: dict[str, str], notifications: socket.socket) -> None:
        super().__init__(config.discord, intents=discord.Intents.none())
        self.config = config
        self.notifications = notifications
        self.login: dict[str, object] = {}

    async def start(self, token: str, *, reconnect: bool = True) -> None:
        self.login = {
            "token": token,
            "notified": self.notifications.recv(64),
            "commands": [command.name for command in self.tree.get_commands()],
        }


class BrokenSprite(FakeSprite):
    extension_names = ("discord_fixtures.broken",)


@pytest.fixture
def instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SPRITE_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("SPRITE_SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.delenv("SPRITE_INSTANCE", raising=False)
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "discord_token").write_text("abc.def\n")
    return tmp_path


@pytest.fixture
def notifications(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[socket.socket]:
    """systemd's end of `$NOTIFY_SOCKET`."""
    path = tmp_path / "notify"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(str(path))
        receiver.setblocking(False)
        monkeypatch.setenv("NOTIFY_SOCKET", str(path))
        yield receiver


def test_starts_with_config_and_token_after_extensions_and_ready(instance: Path, notifications: socket.socket):
    (instance / "config.toml").write_text('greeting = "hi"\n')
    bots: list[FakeSprite] = []

    def make_bot(config: BotConfig, secrets: dict[str, str]) -> FakeSprite:
        bots.append(FakeSprite(config, secrets, notifications))
        return bots[0]

    assert run("sprite-compile", BotConfig, SECRETS, make_bot, argv=[]) == 0
    assert bots[0].config.greeting == "hi"
    assert bots[0].login == {"token": "abc.def", "notified": b"READY=1", "commands": ["ping"]}


def test_broken_extension_fails_before_ready(instance: Path, notifications: socket.socket):
    def make_bot(config: BotConfig, secrets: dict[str, str]) -> FakeSprite:
        return BrokenSprite(config, secrets, notifications)

    with pytest.raises(commands.ExtensionFailed, match="Duplicate command"):
        run("sprite-compile", BotConfig, SECRETS, make_bot, argv=[])
    with pytest.raises(BlockingIOError):
        notifications.recv(64)


def unreachable(config: BotConfig, secrets: dict[str, str]) -> FakeSprite:
    raise AssertionError("the bot must not be built")


def test_invalid_config_exits_naming_the_key(instance: Path, capsys: pytest.CaptureFixture[str]):
    (instance / "config.toml").write_text("greting = 'hi'\n")

    assert run("sprite-compile", BotConfig, SECRETS, unreachable, argv=[]) == 1
    assert "greting" in capsys.readouterr().err


def test_missing_token_exits_naming_it(instance: Path, capsys: pytest.CaptureFixture[str]):
    (instance / "secrets" / "discord_token").unlink()

    assert run("sprite-compile", BotConfig, SECRETS, unreachable, argv=[]) == 1
    assert "discord_token" in capsys.readouterr().err


def test_config_sync_notes_the_installed_version(instance: Path):
    assert run("sprite-compile", BotConfig, SECRETS, unreachable, argv=["config", "sync"]) == 0
    assert "(added in 0.1.0)" in (instance / "config.toml").read_text()


def test_ready_reaches_an_abstract_socket(monkeypatch: pytest.MonkeyPatch):
    name = f"sprite-compile-test-{uuid.uuid4()}"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind("\0" + name)
        monkeypatch.setenv("NOTIFY_SOCKET", "@" + name)

        notify_ready()

        assert receiver.recv(64) == b"READY=1"
