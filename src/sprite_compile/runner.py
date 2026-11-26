"""`run()`: a sprite's command. Without arguments it starts the bot; `config sync|check` and `secrets list` manage the
instance. A bot's `__main__`:

    def main() -> int:
        return run("chance-sprite", ChanceConfig, SECRETS, make_bot)

`app_name` is the bot's distribution name: its installed version is what config sync writes next to new keys, and it
names the platformdirs folders in development.

Start order: logging, config, secrets, the bot (`make_bot`), its extensions, then READY, then the Discord login. A
release that can't get that far is rolled back by auto-update; failures after READY (Discord unreachable, an expired
token) are treated as transient and left to systemd's restarts.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import logging
import os
import sys
from collections.abc import Callable, Mapping, Sequence

from .config import ConfigError, ConfigSection, load_config
from .config_sync import run_config_command
from .discord_sprite import DiscordSprite
from .paths import config_path, secrets_dir
from .secrets import MissingSecretsError, Secret, load_secrets, run_secrets_command
from .systemd import notify_ready, under_systemd

log = logging.getLogger(__name__)

TOKEN_SECRET = "discord_token"


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


async def start(bot: DiscordSprite, token: str) -> None:
    async with bot:
        await bot.load_extensions()
        notify_ready()
        await bot.start(token)


def run[C: ConfigSection](
    app_name: str,
    config_type: type[C],
    secrets: Mapping[str, Secret],
    make_bot: Callable[[C, dict[str, str]], DiscordSprite],
    argv: Sequence[str] | None = None,
) -> int:
    """Run the command line in `argv` (default `sys.argv[1:]`); returns the exit status."""
    if TOKEN_SECRET not in secrets or not secrets[TOKEN_SECRET].required:
        raise ValueError(f"{app_name} must declare the required secret {TOKEN_SECRET!r}")
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
    bot = make_bot(config, secret_values)
    asyncio.run(start(bot, secret_values[TOKEN_SECRET]))
    return 0
