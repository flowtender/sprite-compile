from __future__ import annotations

from pathlib import Path

import pytest

from sprite_compile.secrets import MissingSecretsError, Secret, load_secrets, run_secrets_command

SECRETS = {
    "discord_token": Secret("Bot token from the Developer Portal"),
    "calendar_key": Secret("Google service account JSON"),
    "gmail_oauth": Secret("OAuth client JSON", required=False),
}


def write_secret(directory: Path, name: str, value: str) -> None:
    (directory / name).write_text(value, encoding="utf-8")


def test_loads_values_without_trailing_newline(tmp_path: Path):
    write_secret(tmp_path, "discord_token", "abc.def\n")
    write_secret(tmp_path, "calendar_key", '{"key": "line one\\nline two"}\r\n')

    assert load_secrets(SECRETS, tmp_path) == {
        "discord_token": "abc.def",
        "calendar_key": '{"key": "line one\\nline two"}',
    }


def test_missing_and_empty_required_secrets_are_all_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SPRITE_INSTANCE", raising=False)
    write_secret(tmp_path, "calendar_key", "\n")

    with pytest.raises(MissingSecretsError) as error:
        load_secrets(SECRETS, tmp_path)
    assert "discord_token, calendar_key" in str(error.value)
    assert "gmail_oauth" not in str(error.value)
    assert str(tmp_path) in str(error.value)


def test_deployed_instance_is_told_the_command_to_fix_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPRITE_INSTANCE", "chance-runnerhub")

    with pytest.raises(MissingSecretsError, match="`sprite-register secrets chance-runnerhub`"):
        load_secrets(SECRETS, tmp_path)


def test_list_shows_status_without_values(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    write_secret(tmp_path, "discord_token", "very-secret-value")

    assert run_secrets_command("list", SECRETS, tmp_path) == 1
    output = capsys.readouterr().out
    assert "very-secret-value" not in output
    assert "discord_token  set" in output
    assert "calendar_key   missing" in output
    assert "gmail_oauth    not set (optional)" in output
    assert "calendar_key" in output.splitlines()[-1]


def test_list_succeeds_when_only_optional_secrets_are_missing(tmp_path: Path):
    write_secret(tmp_path, "discord_token", "abc")
    write_secret(tmp_path, "calendar_key", "{}")

    assert run_secrets_command("list", SECRETS, tmp_path) == 0
