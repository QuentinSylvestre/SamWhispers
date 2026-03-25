"""TOML configuration loading and validation."""

from __future__ import annotations

import logging
import tomllib
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("samwhispers")

_VALID_MODES = ("hold", "toggle")
_VALID_PROVIDERS = ("openai", "anthropic")

# ISO 639-1 codes supported by whisper.cpp, plus "auto" for auto-detection
WHISPER_LANGUAGES = {
    "auto",
    "en",
    "zh",
    "de",
    "es",
    "ru",
    "ko",
    "fr",
    "ja",
    "pt",
    "tr",
    "pl",
    "ca",
    "nl",
    "ar",
    "sv",
    "it",
    "id",
    "hi",
    "fi",
    "vi",
    "he",
    "uk",
    "el",
    "ms",
    "cs",
    "ro",
    "da",
    "hu",
    "ta",
    "no",
    "th",
    "ur",
    "hr",
    "bg",
    "lt",
    "la",
    "mi",
    "ml",
    "cy",
    "sk",
    "te",
    "fa",
    "lv",
    "bn",
    "sr",
    "az",
    "sl",
    "kn",
    "et",
    "mk",
    "br",
    "eu",
    "is",
    "hy",
    "ne",
    "mn",
    "bs",
    "kk",
    "sq",
    "sw",
    "gl",
    "mr",
    "pa",
    "si",
    "km",
    "sn",
    "yo",
    "so",
    "af",
    "oc",
    "ka",
    "be",
    "tg",
    "sd",
    "gu",
    "am",
    "yi",
    "lo",
    "uz",
    "fo",
    "ht",
    "ps",
    "tk",
    "nn",
    "mt",
    "sa",
    "lb",
    "my",
    "bo",
    "tl",
    "mg",
    "as",
    "tt",
    "haw",
    "ln",
    "ha",
    "ba",
    "jw",
    "su",
    "yue",
}


@dataclass
class HotkeyConfig:
    key: str = "ctrl+shift+space"
    mode: str = "hold"
    language_key: str = "ctrl+shift+l"


@dataclass
class WhisperConfig:
    server_url: str = "http://localhost:8080"
    languages: list[str] = field(default_factory=lambda: ["auto"])
    managed: bool = True
    server_bin: str = "tools/whisper.cpp/build/bin/whisper-server"
    model_path: str = "tools/whisper.cpp/models/ggml-base.en.bin"


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    max_duration: float = 300.0


@dataclass
class OpenAIConfig:
    api_key: str = ""
    model: str = "gpt-4o-mini"
    api_base: str = "https://api.openai.com/v1"


@dataclass
class AnthropicConfig:
    api_key: str = ""
    model: str = "claude-sonnet-4-20250514"
    api_base: str = "https://api.anthropic.com"


@dataclass
class CleanupConfig:
    enabled: bool = False
    provider: str = "openai"
    openai: OpenAIConfig = field(default_factory=OpenAIConfig)
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)


@dataclass
class InjectConfig:
    paste_delay: float = 0.1


@dataclass
class AppConfig:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    inject: InjectConfig = field(default_factory=InjectConfig)


def find_config() -> Path | None:
    """Search CWD then ~/.config/samwhispers/ for config.toml."""
    candidates = [
        Path("config.toml"),
        Path.home() / ".config" / "samwhispers" / "config.toml",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _merge(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge overrides into defaults."""
    result = dict(defaults)
    for k, v in overrides.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _merge(result[k], v)
        else:
            result[k] = v
    return result


def _validate(config: AppConfig) -> None:
    """Validate config values, raise ValueError on invalid."""
    if config.hotkey.mode not in _VALID_MODES:
        raise ValueError(
            f"Invalid hotkey mode {config.hotkey.mode!r}, must be one of {_VALID_MODES}"
        )
    if not config.whisper.languages:
        raise ValueError("whisper.languages must contain at least one entry")
    for lang in config.whisper.languages:
        if lang not in WHISPER_LANGUAGES:
            raise ValueError(
                f"Invalid language {lang!r}, must be 'auto' or a whisper.cpp language code"
            )
    if config.whisper.managed:
        import os

        bin_path = Path(config.whisper.server_bin)
        if not bin_path.is_file():
            raise ValueError(
                f"whisper.server_bin not found: {bin_path.resolve()}. "
                "Build whisper.cpp first (see README) or set whisper.managed = false."
            )
        if not os.access(bin_path, os.X_OK):
            raise ValueError(
                f"whisper.server_bin is not executable: {bin_path.resolve()}. "
                "Run: chmod +x " + str(bin_path.resolve())
            )
        model_path = Path(config.whisper.model_path)
        if not model_path.is_file():
            raise ValueError(
                f"whisper.model_path not found: {model_path.resolve()}. "
                "Download a model first (see README) or set whisper.managed = false."
            )
    if config.cleanup.provider not in _VALID_PROVIDERS:
        raise ValueError(
            f"Invalid cleanup provider {config.cleanup.provider!r}, "
            f"must be one of {_VALID_PROVIDERS}"
        )
    if config.cleanup.enabled:
        key = (
            config.cleanup.openai.api_key
            if config.cleanup.provider == "openai"
            else config.cleanup.anthropic.api_key
        )
        if not key:
            warnings.warn(
                f"Cleanup enabled but {config.cleanup.provider} API key is empty",
                UserWarning,
                stacklevel=3,
            )


def load_config(path: Path | str | None = None) -> AppConfig:
    """Load TOML config, merge with defaults, validate."""
    raw: dict[str, Any] = {}
    if path is not None:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Config file not found: {p}")
        raw = tomllib.loads(p.read_text())
        log.info("Loaded config from %s", p)
    else:
        found = find_config()
        if found:
            raw = tomllib.loads(found.read_text())
            log.info("Loaded config from %s", found)
        else:
            log.info("No config file found, using defaults")

    # Build config from defaults merged with file values
    defaults = AppConfig()

    # Backward compat: whisper.language (str) -> whisper.languages (list)
    whisper_raw = raw.get("whisper", {})
    if "language" in whisper_raw and "languages" not in whisper_raw:
        whisper_raw["languages"] = [whisper_raw.pop("language")]
    elif "language" in whisper_raw and "languages" in whisper_raw:
        whisper_raw.pop("language")  # languages takes precedence

    d = _merge(_to_dict(defaults), raw)

    config = AppConfig(
        hotkey=HotkeyConfig(**d.get("hotkey", {})),
        whisper=WhisperConfig(**d.get("whisper", {})),
        audio=AudioConfig(**d.get("audio", {})),
        cleanup=CleanupConfig(
            enabled=d.get("cleanup", {}).get("enabled", False),
            provider=d.get("cleanup", {}).get("provider", "openai"),
            openai=OpenAIConfig(**d.get("cleanup", {}).get("openai", {})),
            anthropic=AnthropicConfig(**d.get("cleanup", {}).get("anthropic", {})),
        ),
        inject=InjectConfig(**d.get("inject", {})),
    )
    _validate(config)
    return config


def _to_dict(obj: Any) -> dict[str, Any]:
    """Convert nested dataclass to dict."""
    from dataclasses import asdict

    result: dict[str, Any] = asdict(obj)
    return result
