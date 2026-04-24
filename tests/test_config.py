"""Tests for configuration module."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from samwhispers.config import AppConfig, load_config


def test_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading with no file returns valid defaults."""
    monkeypatch.chdir(tmp_path)
    # Write minimal config to disable managed mode (avoids binary/model validation)
    (tmp_path / "config.toml").write_text("[whisper]\nmanaged = false\n")
    config = load_config()
    assert config.hotkey.key == "ctrl+shift+space"
    assert config.hotkey.mode == "hold"
    assert config.hotkey.language_key == "ctrl+shift+l"
    assert config.whisper.server_url == "http://localhost:8080"
    assert config.whisper.languages == ["auto"]
    assert config.audio.sample_rate == 16000
    assert config.cleanup.enabled is False
    assert config.inject.paste_delay == 0.1
    assert config.postprocess.collapse_newlines is True
    assert config.postprocess.collapse_spaces is True
    assert config.postprocess.trim is True
    assert config.postprocess.trailing == "newline"


def test_load_valid_toml(tmp_path: Path) -> None:
    """Load a valid TOML config file."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[hotkey]\nkey = "alt+r"\nmode = "toggle"\n'
        '[whisper]\nserver_url = "http://localhost:9090"\nmanaged = false\n'
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
    cfg.write_text("[audio]\nmax_duration = 60.0\n[whisper]\nmanaged = false\n")
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
    cfg.write_text('[cleanup]\nprovider = "google"\n[whisper]\nmanaged = false\n')
    with pytest.raises(ValueError, match="Invalid cleanup provider"):
        load_config(cfg)


def test_cleanup_without_key_warns(tmp_path: Path) -> None:
    """Cleanup enabled with empty API key emits warning."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[cleanup]\nenabled = true\nprovider = "openai"\n[whisper]\nmanaged = false\n')
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
        '[cleanup]\nenabled = true\nprovider = "openai"\n'
        '[cleanup.openai]\napi_key = "sk-test"\n'
        "[whisper]\nmanaged = false\n"
    )
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        load_config(cfg)
        assert len(w) == 0


def test_full_config(tmp_path: Path) -> None:
    """Full config with old language field loads via backward compat."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[hotkey]\nkey = "ctrl+shift+space"\nmode = "hold"\n'
        '[whisper]\nserver_url = "http://localhost:8080"\nlanguage = "en"\nmanaged = false\n'
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
    assert config.whisper.languages == ["en"]


def test_languages_list(tmp_path: Path) -> None:
    """Languages list is loaded correctly."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[whisper]\nlanguages = ["auto", "en", "fr"]\nmanaged = false\n')
    config = load_config(cfg)
    assert config.whisper.languages == ["auto", "en", "fr"]


def test_invalid_language_rejected(tmp_path: Path) -> None:
    """Invalid language code raises ValueError."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[whisper]\nlanguages = ["en", "zzzz"]\n')
    with pytest.raises(ValueError, match="Invalid language"):
        load_config(cfg)


def test_empty_languages_rejected(tmp_path: Path) -> None:
    """Empty languages list raises ValueError."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("[whisper]\nlanguages = []\n")
    with pytest.raises(ValueError, match="must contain at least one entry"):
        load_config(cfg)


def test_backward_compat_language_to_languages(tmp_path: Path) -> None:
    """Old language string is converted to languages list."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[whisper]\nlanguage = "fr"\nmanaged = false\n')
    config = load_config(cfg)
    assert config.whisper.languages == ["fr"]


def test_languages_takes_precedence_over_language(tmp_path: Path) -> None:
    """When both language and languages are present, languages wins."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[whisper]\nlanguage = "en"\nlanguages = ["fr", "de"]\nmanaged = false\n')
    config = load_config(cfg)
    assert config.whisper.languages == ["fr", "de"]


def test_language_key_config(tmp_path: Path) -> None:
    """Language key hotkey is configurable."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[hotkey]\nlanguage_key = "alt+l"\n[whisper]\nmanaged = false\n')
    config = load_config(cfg)
    assert config.hotkey.language_key == "alt+l"


def test_whisper_config_defaults() -> None:
    """WhisperConfig dataclass has correct defaults for managed mode fields."""
    from samwhispers.config import WhisperConfig

    wc = WhisperConfig()
    assert wc.managed is True
    assert wc.server_bin == "tools/whisper.cpp/build/bin/whisper-server"
    assert wc.model_path == "tools/whisper.cpp/models/ggml-base.en.bin"


def test_managed_missing_binary_raises(tmp_path: Path) -> None:
    """managed=true with missing binary raises ValueError."""
    cfg = tmp_path / "config.toml"
    # Use explicit non-existent paths with forward slashes (TOML-safe)
    cfg.write_text(
        "[whisper]\nmanaged = true\n"
        'server_bin = "/nonexistent/whisper-server"\n'
        'model_path = "/nonexistent/model.bin"\n'
    )
    with pytest.raises(ValueError, match="whisper.server_bin not found"):
        load_config(cfg)


def test_managed_missing_model_raises(tmp_path: Path) -> None:
    """managed=true with missing model raises ValueError."""
    cfg = tmp_path / "config.toml"
    # Create a fake binary so we pass the binary check
    bin_path = tmp_path / "whisper-server"
    bin_path.write_bytes(b"fake")
    bin_path.chmod(0o755)
    model_path = tmp_path / "nonexistent.bin"
    # Use forward slashes for TOML compatibility on Windows
    bin_str = str(bin_path).replace("\\", "/")
    model_str = str(model_path).replace("\\", "/")
    cfg.write_text(
        f'[whisper]\nmanaged = true\nserver_bin = "{bin_str}"\nmodel_path = "{model_str}"\n'
    )
    with pytest.raises(ValueError, match="whisper.model_path not found"):
        load_config(cfg)


def test_managed_false_skips_validation(tmp_path: Path) -> None:
    """managed=false skips binary/model validation regardless of file existence."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[whisper]\nmanaged = false\n"
        'server_bin = "/nonexistent/whisper-server"\n'
        'model_path = "/nonexistent/model.bin"\n'
    )
    config = load_config(cfg)
    assert config.whisper.managed is False


def test_managed_nonexecutable_binary_raises(tmp_path: Path) -> None:
    """managed=true with non-executable binary raises ValueError on non-Windows."""
    import sys

    if sys.platform == "win32":
        pytest.skip("os.access X_OK always True on Windows")
    cfg = tmp_path / "config.toml"
    bin_path = tmp_path / "whisper-server"
    bin_path.write_bytes(b"fake")
    bin_path.chmod(0o644)  # not executable
    cfg.write_text(f'[whisper]\nmanaged = true\nserver_bin = "{bin_path}"\n')
    with pytest.raises(ValueError, match="not executable"):
        load_config(cfg)


def test_invalid_server_url_scheme_raises(tmp_path: Path) -> None:
    """Invalid server_url scheme raises ValueError."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[whisper]\nserver_url = "ftp://localhost:8080"\nmanaged = false\n')
    with pytest.raises(ValueError, match="Invalid whisper.server_url scheme"):
        load_config(cfg)


def test_invalid_server_url_port_raises(tmp_path: Path) -> None:
    """Invalid server_url port raises ValueError."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[whisper]\nserver_url = "http://localhost:99999"\nmanaged = false\n')
    with pytest.raises(ValueError, match="whisper.server_url port"):
        load_config(cfg)


def test_valid_server_url_accepted(tmp_path: Path) -> None:
    """Valid http server_url passes validation."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[whisper]\nserver_url = "http://127.0.0.1:9090"\nmanaged = false\n')
    config = load_config(cfg)
    assert config.whisper.server_url == "http://127.0.0.1:9090"


def test_invalid_trailing_raises(tmp_path: Path) -> None:
    """Invalid postprocess.trailing raises ValueError."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[postprocess]\ntrailing = "invalid"\n[whisper]\nmanaged = false\n')
    with pytest.raises(ValueError, match="Invalid postprocess.trailing"):
        load_config(cfg)


# --- Phase 1: Vocabulary config tests ---


def test_vocabulary_global_words(tmp_path: Path) -> None:
    """Load config with global vocabulary words."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[whisper]\nmanaged = false\n[vocabulary]\nwords = ["RSSI", "pynput"]\n')
    config = load_config(cfg)
    assert config.vocabulary.words == ["RSSI", "pynput"]


def test_vocabulary_per_language(tmp_path: Path) -> None:
    """Load config with per-language vocabulary words."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[whisper]\nmanaged = false\n"
        '[vocabulary]\nwords = ["RSSI"]\n'
        '[vocabulary.fr]\nwords = ["BLE"]\n'
    )
    config = load_config(cfg)
    assert config.vocabulary.words == ["RSSI"]
    assert config.vocabulary.languages["fr"] == ["BLE"]


def test_vocabulary_invalid_language(tmp_path: Path) -> None:
    """Invalid vocabulary language code raises ValueError."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[whisper]\nmanaged = false\n[vocabulary.zzzz]\nwords = ["test"]\n')
    with pytest.raises(ValueError, match="Invalid vocabulary language"):
        load_config(cfg)


def test_vocabulary_auto_language_rejected(tmp_path: Path) -> None:
    """Vocabulary language 'auto' is rejected."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[whisper]\nmanaged = false\n[vocabulary.auto]\nwords = ["test"]\n')
    with pytest.raises(ValueError, match="Invalid vocabulary language"):
        load_config(cfg)


def test_vocabulary_empty_default(tmp_path: Path) -> None:
    """No vocabulary section gives empty defaults."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("[whisper]\nmanaged = false\n")
    config = load_config(cfg)
    assert config.vocabulary.words == []
    assert config.vocabulary.languages == {}


def test_vocabulary_merged_with_defaults(tmp_path: Path) -> None:
    """Partial vocabulary config merges correctly with defaults."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[whisper]\nmanaged = false\n[vocabulary]\nwords = ["RSSI"]\n')
    config = load_config(cfg)
    assert config.vocabulary.words == ["RSSI"]
    assert config.vocabulary.languages == {}
