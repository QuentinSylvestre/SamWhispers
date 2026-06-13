"""Tests for the web config read/validate/write helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from samwhispers.config import build_config
from samwhispers.webconfig import (
    load_config_dict,
    requires_restart,
    save_config_dict,
    to_toml_dict,
    validate_config_dict,
)


def test_load_defaults_when_no_file(tmp_path: Path) -> None:
    data = load_config_dict(tmp_path / "missing.toml")
    assert data["hotkey"]["key"] == "ctrl+shift+space"
    assert data["whisper"]["languages"] == ["auto"]
    # Vocabulary is laid out in TOML shape (no "languages" key).
    assert "languages" not in data["vocabulary"]
    assert data["history"]["enabled"] is True


def test_history_settings_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    data = load_config_dict(path)
    data["whisper"]["managed"] = False
    data["history"]["enabled"] = False
    data["history"]["max_entries"] = 50
    save_config_dict(data, path)
    reloaded = load_config_dict(path)
    assert reloaded["history"]["enabled"] is False
    assert reloaded["history"]["max_entries"] == 50


def test_translation_settings_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    data = load_config_dict(path)
    data["whisper"]["managed"] = False
    data["translation"]["enabled"] = True
    data["translation"]["target_language"] = "fr"
    save_config_dict(data, path)
    reloaded = load_config_dict(path)
    assert reloaded["translation"]["enabled"] is True
    assert reloaded["translation"]["target_language"] == "fr"


def test_translation_rejects_auto_target(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    data = load_config_dict(path)
    data["whisper"]["managed"] = False
    data["translation"]["target_language"] = "auto"
    with pytest.raises(ValueError):
        save_config_dict(data, path)


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    data = load_config_dict(path)
    data["hotkey"]["key"] = "ctrl+alt+s"
    data["whisper"]["languages"] = ["en", "fr"]
    data["whisper"]["managed"] = False  # avoid binary/model file checks
    save_config_dict(data, path)

    assert path.is_file()
    reloaded = load_config_dict(path)
    assert reloaded["hotkey"]["key"] == "ctrl+alt+s"
    assert reloaded["whisper"]["languages"] == ["en", "fr"]


def test_per_language_vocabulary_survives_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    data = load_config_dict(path)
    data["whisper"]["managed"] = False
    data["vocabulary"]["words"] = ["Bluetooth"]
    data["vocabulary"]["en"] = {"words": ["GitHub", "OAuth"]}
    save_config_dict(data, path)

    reloaded = load_config_dict(path)
    assert reloaded["vocabulary"]["words"] == ["Bluetooth"]
    assert reloaded["vocabulary"]["en"]["words"] == ["GitHub", "OAuth"]


def test_save_rejects_invalid_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    data = load_config_dict(path)
    data["hotkey"]["mode"] = "nonsense"
    with pytest.raises(ValueError):
        save_config_dict(data, path)
    assert not path.is_file()  # nothing written on validation failure


def test_to_toml_dict_shape() -> None:
    cfg = build_config({"whisper": {"managed": False}, "vocabulary": {"words": ["x"]}})
    out = to_toml_dict(cfg)
    assert out["cleanup"]["openai"]["model"]
    assert out["vocabulary"]["words"] == ["x"]


def test_requires_restart_detects_change() -> None:
    base = build_config({"whisper": {"managed": False}})
    same = build_config({"whisper": {"managed": False}})
    changed = build_config({"whisper": {"managed": False}, "hotkey": {"key": "ctrl+x"}})
    assert requires_restart(base, same) is False
    assert requires_restart(base, changed) is True


def test_validate_config_dict_returns_appconfig() -> None:
    cfg = validate_config_dict({"whisper": {"managed": False}, "audio": {"sample_rate": 8000}})
    assert cfg.audio.sample_rate == 8000
