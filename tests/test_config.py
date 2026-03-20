"""Tests for configuration module."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from samwhispers.config import AppConfig, load_config


def test_defaults() -> None:
    """Loading with no file returns valid defaults."""
    config = load_config()
    assert config.hotkey.key == "ctrl+shift+space"
    assert config.hotkey.mode == "hold"
    assert config.whisper.server_url == "http://localhost:8080"
    assert config.audio.sample_rate == 16000
    assert config.cleanup.enabled is False
    assert config.inject.paste_delay == 0.1


def test_load_valid_toml(tmp_path: Path) -> None:
    """Load a valid TOML config file."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[hotkey]\nkey = "alt+r"\nmode = "toggle"\n'
        '[whisper]\nserver_url = "http://localhost:9090"\n'
    )
    config = load_config(cfg)
    assert config.hotkey.key == "alt+r"
    assert config.hotkey.mode == "toggle"
    assert config.whisper.server_url == "http://localhost:9090"
    # Unset values keep defaults
    assert config.audio.sample_rate == 16000


def test_partial_config(tmp_path: Path) -> None:
    """Partial config merges with defaults."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("[audio]\nmax_duration = 60.0\n")
    config = load_config(cfg)
    assert config.audio.max_duration == 60.0
    assert config.audio.sample_rate == 16000  # default preserved


def test_missing_file_raises() -> None:
    """Explicit path to missing file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/config.toml")


def test_invalid_mode(tmp_path: Path) -> None:
    """Invalid hotkey mode raises ValueError."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[hotkey]\nmode = "bad"\n')
    with pytest.raises(ValueError, match="Invalid hotkey mode"):
        load_config(cfg)


def test_invalid_provider(tmp_path: Path) -> None:
    """Invalid cleanup provider raises ValueError."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[cleanup]\nprovider = "google"\n')
    with pytest.raises(ValueError, match="Invalid cleanup provider"):
        load_config(cfg)


def test_cleanup_without_key_warns(tmp_path: Path) -> None:
    """Cleanup enabled with empty API key emits warning."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[cleanup]\nenabled = true\nprovider = "openai"\n')
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        config = load_config(cfg)
        assert config.cleanup.enabled is True
        assert len(w) == 1
        assert "API key is empty" in str(w[0].message)


def test_cleanup_with_key_no_warning(tmp_path: Path) -> None:
    """Cleanup enabled with API key does not warn."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[cleanup]\nenabled = true\nprovider = "openai"\n[cleanup.openai]\napi_key = "sk-test"\n'
    )
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        load_config(cfg)
        assert len(w) == 0


def test_full_config(tmp_path: Path) -> None:
    """Full config matching config.example.toml loads correctly."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[hotkey]\nkey = "ctrl+shift+space"\nmode = "hold"\n'
        '[whisper]\nserver_url = "http://localhost:8080"\nlanguage = "en"\n'
        "[audio]\nsample_rate = 16000\nmax_duration = 300.0\n"
        '[cleanup]\nenabled = false\nprovider = "openai"\n'
        '[cleanup.openai]\napi_key = ""\nmodel = "gpt-4o-mini"\n'
        'api_base = "https://api.openai.com/v1"\n'
        '[cleanup.anthropic]\napi_key = ""\nmodel = "claude-sonnet-4-20250514"\n'
        'api_base = "https://api.anthropic.com"\n'
        "[inject]\npaste_delay = 0.1\n"
    )
    config = load_config(cfg)
    assert isinstance(config, AppConfig)
