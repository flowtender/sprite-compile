"""Secrets: declared in code per bot, read from one file per secret (Podman mounts them at `/run/secrets/<name>`).

    SECRETS = {
        "discord_token": Secret("Bot token from the Developer Portal"),
        "gmail_oauth": Secret("OAuth client JSON", required=False),
    }

A missing or empty file counts as not set. Values are never printed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .paths import instance_name


@dataclass(frozen=True)
class Secret:
    description: str
    required: bool = True


class MissingSecretsError(Exception):
    pass


def read_secret(directory: Path, name: str) -> str | None:
    """The secret's value without its trailing newline; None when the file is missing or empty."""
    try:
        value = (directory / name).read_text(encoding="utf-8").rstrip("\r\n")
    except FileNotFoundError:
        return None
    return value or None


def missing_required(declarations: Mapping[str, Secret], values: Mapping[str, str | None]) -> list[str]:
    return [name for name, secret in declarations.items() if secret.required and values[name] is None]


def missing_message(missing: list[str], directory: Path) -> str:
    names = ", ".join(missing)
    if instance := instance_name():
        return f"Required secrets not set: {names}. Set them with `sprite-register secrets {instance}`."
    return f"Required secrets not set: {names}. Put each in a file named after it in {directory}."


def load_secrets(declarations: Mapping[str, Secret], directory: Path) -> dict[str, str]:
    """The values of all set secrets; raises naming every required secret that isn't set."""
    values = {name: read_secret(directory, name) for name in declarations}
    if missing := missing_required(declarations, values):
        raise MissingSecretsError(missing_message(missing, directory))
    return {name: value for name, value in values.items() if value is not None}


def run_secrets_command(action: str, declarations: Mapping[str, Secret], directory: Path) -> int:
    """`secrets list`: each declared secret with whether it's set; exits 1 when a required one isn't."""
    if action != "list":
        print(f"Unknown secrets command: {action!r} (expected list)")
        return 2
    values = {name: read_secret(directory, name) for name in declarations}
    width = max((len(name) for name in declarations), default=0)
    for name, secret in declarations.items():
        status = "set" if values[name] is not None else "missing" if secret.required else "not set (optional)"
        print(f"{name:<{width}}  {status:<18}  {secret.description}")
    if missing := missing_required(declarations, values):
        print(missing_message(missing, directory))
        return 1
    return 0
