from __future__ import annotations

import asyncio
import os
import signal
import socket
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import pytest

from sprite_compile import runner
from sprite_compile.config import ConfigSection, Setting
from sprite_compile.runner import run
from sprite_compile.secrets import Secret
from sprite_compile.systemd import notify_ready

SECRETS = {"chat_token": Secret("Token for the chat platform")}


class BotConfig(ConfigSection):
    greeting: Annotated[str, Setting("What the bot says.", added="0.1.0")] = "hello"


class PlatformDown(Exception):
    pass


class Clock:
    def __init__(self) -> None:
        self.now = 0.0


class Notifications:
    """systemd's end of `$NOTIFY_SOCKET`."""

    def __init__(self, receiver: socket.socket) -> None:
        self.receiver = receiver
        self.ready = False

    def is_ready(self) -> bool:
        if not self.ready:
            try:
                self.ready = self.receiver.recv(64) == b"READY=1"
            except BlockingIOError:
                pass
        return self.ready


class FakeFrontend:
    """Each `serve()` runs for the next entry of `failures` (seconds of fake time) and then fails; once they're used
    up, `serve()` stops normally."""

    def __init__(self, notifications: Notifications, clock: Clock, failures: list[float] | None = None) -> None:
        self.notifications = notifications
        self.clock = clock
        self.failures = list(failures or [])
        self.events: list[str] = []

    async def prepare(self) -> None:
        self.events.append("prepare")

    async def serve(self) -> None:
        self.events.append("serve after READY" if self.notifications.is_ready() else "serve before READY")
        if self.failures:
            self.clock.now += self.failures.pop(0)
            raise PlatformDown


class BrokenFrontend(FakeFrontend):
    async def prepare(self) -> None:
        raise RuntimeError("Duplicate command")


class StoppedBySignalFrontend(FakeFrontend):
    """Serves until the process is sent SIGTERM, then records its cleanup."""

    async def serve(self) -> None:
        self.events.append("serve")
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.Event().wait()
        finally:
            self.events.append("cleanup")


@pytest.fixture
def instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SPRITE_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("SPRITE_SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.delenv("SPRITE_INSTANCE", raising=False)
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "chat_token").write_text("abc.def\n")
    return tmp_path


@pytest.fixture
def notifications(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Notifications]:
    path = tmp_path / "notify"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(str(path))
        receiver.setblocking(False)
        monkeypatch.setenv("NOTIFY_SOCKET", str(path))
        yield Notifications(receiver)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock.now)
    return clock


@pytest.fixture
def delays(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> list[float]:
    """The retry delays `run()` waits, passing on the fake clock instead of waiting."""
    waited: list[float] = []

    async def sleep(seconds: float) -> None:
        waited.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(runner.asyncio, "sleep", sleep)
    return waited


def run_with(frontends: list[FakeFrontend], seen: dict | None = None) -> int:
    def make_frontends(config: BotConfig, secrets: dict[str, str]) -> list[FakeFrontend]:
        if seen is not None:
            seen.update(greeting=config.greeting, secrets=secrets)
        return frontends

    return run("sprite-compile", BotConfig, SECRETS, make_frontends, argv=[])


def test_sigterm_stops_every_frontend_after_its_cleanup_and_exits_cleanly(instance: Path, notifications, clock):
    stopped = StoppedBySignalFrontend(notifications, clock)
    idle = FakeFrontend(notifications, clock)

    assert run_with([stopped, idle]) == 0
    assert stopped.events == ["prepare", "serve", "cleanup"]


def test_prepares_every_frontend_before_ready_and_serves_after(instance: Path, notifications, clock):
    (instance / "config.toml").write_text('greeting = "hi"\n')
    frontends = [FakeFrontend(notifications, clock), FakeFrontend(notifications, clock)]
    seen: dict = {}

    assert run_with(frontends, seen) == 0
    assert seen == {"greeting": "hi", "secrets": {"chat_token": "abc.def"}}
    assert [frontend.events for frontend in frontends] == [["prepare", "serve after READY"]] * 2


def test_failing_prepare_fails_before_ready(instance: Path, notifications, clock):
    frontends = [FakeFrontend(notifications, clock), BrokenFrontend(notifications, clock)]

    with pytest.raises(RuntimeError, match="Duplicate command"):
        run_with(frontends)
    assert not notifications.is_ready()
    assert frontends[0].events == ["prepare"]


def test_failed_frontend_retries_with_growing_delay_while_others_serve(instance: Path, notifications, clock, delays):
    failing = FakeFrontend(notifications, clock, failures=[0] * 8)
    healthy = FakeFrontend(notifications, clock)

    assert run_with([failing, healthy]) == 0
    assert failing.events.count("serve after READY") == 9
    assert healthy.events == ["prepare", "serve after READY"]
    assert delays == [5, 10, 20, 40, 80, 160, 320, 600]


def test_retry_delay_starts_over_after_a_healthy_run(instance: Path, notifications, clock, delays):
    frontend = FakeFrontend(notifications, clock, failures=[0, 0, 3600, 0])

    assert run_with([frontend]) == 0
    assert delays == [5, 10, 5, 10]


def unreachable(config: BotConfig, secrets: dict[str, str]) -> list[FakeFrontend]:
    raise AssertionError("no frontend must be built")


def test_invalid_config_exits_naming_the_key(instance: Path, capsys: pytest.CaptureFixture[str]):
    (instance / "config.toml").write_text("greting = 'hi'\n")

    assert run("sprite-compile", BotConfig, SECRETS, unreachable, argv=[]) == 1
    assert "greting" in capsys.readouterr().err


def test_missing_secret_exits_naming_it(instance: Path, capsys: pytest.CaptureFixture[str]):
    (instance / "secrets" / "chat_token").unlink()

    assert run("sprite-compile", BotConfig, SECRETS, unreachable, argv=[]) == 1
    assert "chat_token" in capsys.readouterr().err


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
