from __future__ import annotations

from pathlib import Path

import pytest

from sprite_compile.paths import cache_dir, config_path, secrets_dir, state_dir


def test_instance_data_dir_holds_both_databases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("SPRITE_DATA_DIR", str(tmp_path))

    assert state_dir("chance-sprite") == tmp_path
    assert cache_dir("chance-sprite") == tmp_path


def test_development_keeps_cache_apart_from_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SPRITE_DATA_DIR", raising=False)

    assert state_dir("chance-sprite") != cache_dir("chance-sprite")


def test_instance_config_file_from_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("SPRITE_CONFIG", str(tmp_path / "config.toml"))

    assert config_path("chance-sprite") == tmp_path / "config.toml"


def test_instance_secrets_dir_from_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPRITE_SECRETS_DIR", "/run/secrets")

    assert secrets_dir("chance-sprite") == Path("/run/secrets")
