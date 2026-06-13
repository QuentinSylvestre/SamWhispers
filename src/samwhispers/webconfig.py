"""Config read/validate/write helpers for the web UI.

Bridges the dataclass config (``samwhispers.config``) and the JSON the browser
sends, and serialises back to TOML on save. Validation goes through the same
``build_config`` path as file loading, so the UI can never persist a config the
daemon would reject.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from samwhispers.config import AppConfig, build_config, find_config

log = logging.getLogger("samwhispers.web")

_DEFAULT_CONFIG_PATH = Path.home() / ".config" / "samwhispers" / "config.toml"


def resolve_config_path() -> Path:
    """Where the UI reads/writes config: an existing file, else the default."""
    found = find_config()
    return found if found else _DEFAULT_CONFIG_PATH


def _read_raw(path: Path | str | None) -> dict[str, Any]:
    """Read the raw TOML mapping from disk (empty if the file is absent)."""
    p = Path(path) if path is not None else resolve_config_path()
    if p.is_file():
        import tomllib

        return tomllib.loads(p.read_text(encoding="utf-8"))
    return {}


def current_app_config(path: Path | str | None = None) -> AppConfig:
    """The on-disk config as an AppConfig, *without* strict validation.

    Used to display the current state and to compare against a save, so an
    already-invalid config (e.g. whisper not built yet) doesn't block the UI.
    """
    return build_config(_read_raw(path), validate=False)


def load_config_dict(path: Path | str | None = None) -> dict[str, Any]:
    """Load the effective config (defaults + file) as a TOML-shaped nested dict.

    The shape matches what ``save_config_dict`` writes and what the loader
    expects, so the UI can round-trip the whole object (GET then PUT) without
    dropping fields such as per-language vocabulary. Returns defaults if no
    file exists yet. Note: this includes any configured API keys -- the server
    is bound to loopback only.
    """
    return to_toml_dict(current_app_config(path))


def to_toml_dict(config: AppConfig) -> dict[str, Any]:
    """Convert AppConfig to a dict laid out the way the TOML loader expects.

    The only non-trivial part is vocabulary: per-language word lists live in
    ``[vocabulary.<lang>]`` sub-tables rather than under a ``languages`` key.
    """
    data: dict[str, Any] = {
        "hotkey": asdict(config.hotkey),
        "whisper": asdict(config.whisper),
        "audio": asdict(config.audio),
        "cleanup": {
            "enabled": config.cleanup.enabled,
            "provider": config.cleanup.provider,
            "openai": asdict(config.cleanup.openai),
            "anthropic": asdict(config.cleanup.anthropic),
        },
        "postprocess": asdict(config.postprocess),
        "inject": asdict(config.inject),
        "filler": asdict(config.filler),
        "history": asdict(config.history),
        "translation": asdict(config.translation),
    }

    vocab: dict[str, Any] = {"words": list(config.vocabulary.words)}
    for lang, words in config.vocabulary.languages.items():
        vocab[lang] = {"words": list(words)}
    data["vocabulary"] = vocab
    return data


def validate_config_dict(raw: dict[str, Any]) -> AppConfig:
    """Validate a posted config mapping, raising ValueError if invalid."""
    return build_config(dict(raw))


def save_config_dict(raw: dict[str, Any], path: Path | str | None = None) -> AppConfig:
    """Validate then atomically write the config to TOML. Returns the AppConfig."""
    import tomli_w

    config = validate_config_dict(raw)
    p = Path(path) if path is not None else resolve_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    toml_text = tomli_w.dumps(to_toml_dict(config))
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(toml_text, encoding="utf-8")
    tmp.replace(p)
    log.info("Saved config to %s", p)
    return config


def requires_restart(old: AppConfig, new: AppConfig) -> bool:
    """Whether applying ``new`` over ``old`` needs a worker restart.

    The worker builds all of its components at startup with no in-process
    reload, so any functional change requires a restart. An unchanged config is
    a no-op (so saving without edits won't bounce the worker).
    """
    return old != new
