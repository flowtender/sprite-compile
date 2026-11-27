"""`run()`: a sprite's command. Without arguments it starts the bot; `config sync|check` and `secrets list` manage the
instance. A bot's `__main__`:

    def make_frontends(config: ChanceConfig, secrets: dict[str, str]) -> list[Frontend]:
        return [ChanceSprite(config.discord, token=secrets["discord_token"], intents=...)]

    def main() -> int:
        return run("chance-sprite", ChanceConfig, SECRETS, make_frontends)

`app_name` is the bot's distribution name: its installed version is what config sync writes next to new keys, and it
names the platformdirs folders in development. The runner knows no chat platform; each frontend (Discord, …) is one
way to reach the bot, and several can run in one process, sharing whatever `make_frontends` gives them.

Start order: logging, config, secrets, `make_frontends`, every frontend's `prepare()`, READY, then every frontend's
`serve()`. A release that can't get to READY is rolled back by auto-update. A frontend whose `serve()` fails after
that (its platform down, an expired token) is retried on its own with a growing delay while the others keep running.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import logging
import os
import signal
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from .config import ConfigError, ConfigSection, load_config
from .config_sync import run_config_command
from .paths import config_path, secrets_dir
from .secrets import MissingSecretsError, Secret, load_secrets, run_secrets_command
from .systemd import notify_ready, under_systemd

log = logging.getLogger(__name__)

RETRY_FIRST_DELAY = 5.0
RETRY_MAX_DELAY = 600.0
# A frontend that served this long before failing was healthy: its retries start over at the first delay.
HEALTHY_AFTER = 600.0


class Frontend(Protocol):
    async def prepare(self) -> None:
        """Everything that fails only because the release is broken (e.g. loading extensions); runs before READY."""

    async def serve(self) -> None:
        """Connect and run until stopped; may be called again after it raised."""


def configure_logging() -> None:
    """Level from `$LOG_LEVEL` (default INFO); no timestamps under systemd, since the journal adds its own."""
    timestamp = "" if under_systemd() else "%(asctime)s "
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format=timestamp + "[%(levelname)-8s] %(name)s: %(message)s",
    )


def parse_arguments(app_name: str, argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=app_name, description="Without a command, starts the bot.")
    commands = parser.add_subparsers(dest="command")
    config = commands.add_parser("config", help="add missing keys to config.toml, or check it")
    config.add_argument("action", choices=["sync", "check"])
    secrets = commands.add_parser("secrets", help="show which secrets are set")
    secrets.add_argument("action", choices=["list"])
    return parser.parse_args(argv)


async def keep_serving(frontend: Frontend) -> None:
    """Serve until `serve()` returns; retry failures with a delay that doubles up to RETRY_MAX_DELAY."""
    name = type(frontend).__name__
    delay = RETRY_FIRST_DELAY
    while True:
        started = time.monotonic()
        try:
            await frontend.serve()
            log.info("%s stopped", name)
            return
        except Exception:
            if time.monotonic() - started >= HEALTHY_AFTER:
                delay = RETRY_FIRST_DELAY
            log.exception("%s failed; retrying in %.0f s", name, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, RETRY_MAX_DELAY)


async def start[C: ConfigSection](
    make_frontends: Callable[[C, dict[str, str]], Sequence[Frontend]], config: C, secrets: dict[str, str]
) -> None:
    """Prepare and serve every frontend until they stop or SIGTERM/SIGINT arrives, which cancels them so their `serve()`
    cleanup runs. Handled explicitly because a container's PID 1 ignores signals it has no handler for."""
    frontends = make_frontends(config, secrets)
    if not frontends:
        raise ValueError("make_frontends returned no frontends")

    main_task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    stop_requested = False

    def request_stop(received: signal.Signals) -> None:
        nonlocal stop_requested
        stop_requested = True
        log.info("Received %s; stopping", received.name)
        main_task.cancel()

    stop_signals = (signal.SIGTERM, signal.SIGINT)
    for stop_signal in stop_signals:
        loop.add_signal_handler(stop_signal, request_stop, stop_signal)
    try:
        for frontend in frontends:
            await frontend.prepare()
        notify_ready()
        await asyncio.gather(*(keep_serving(frontend) for frontend in frontends))
    except asyncio.CancelledError:
        if not stop_requested:
            raise
    finally:
        for stop_signal in stop_signals:
            loop.remove_signal_handler(stop_signal)


def run[C: ConfigSection](
    app_name: str,
    config_type: type[C],
    secrets: Mapping[str, Secret],
    make_frontends: Callable[[C, dict[str, str]], Sequence[Frontend]],
    argv: Sequence[str] | None = None,
) -> int:
    """Run the command line in `argv` (default `sys.argv[1:]`); returns the exit status."""
    arguments = parse_arguments(app_name, argv)
    configure_logging()

    if arguments.command == "config":
        version = importlib.metadata.version(app_name)
        return run_config_command(arguments.action, config_type, config_path(app_name), version)
    if arguments.command == "secrets":
        return run_secrets_command(arguments.action, secrets, secrets_dir(app_name))

    try:
        config = load_config(config_type, config_path(app_name))
        secret_values = load_secrets(secrets, secrets_dir(app_name))
    except (ConfigError, MissingSecretsError) as error:
        print(error, file=sys.stderr)
        return 1
    asyncio.run(start(make_frontends, config, secret_values))
    return 0
