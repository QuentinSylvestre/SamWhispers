"""Config read/validate/write helpers for the web UI.

Bridges the dataclass config (``samwhispers.config``) and the JSON the browser
sends, and serialises back to TOML on save. Validation goes through the same
``build_config`` path as file loading, so the UI can never persist a config the
daemon would reject.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any

from samwhispers.config import AppConfig, build_config, find_config

log = logging.getLogger("samwhispers.web")

_DEFAULT_CONFIG_PATH = Path.home() / ".config" / "samwhispers" / "config.toml"
REDACTED = "__SAMWHISPERS_SECRET_SET__"
SECRET_PATHS = (
    ("cleanup", "openai", "api_key"),
    ("cleanup", "anthropic", "api_key"),
)

# Standard faster-whisper model names (downloaded on first use).
FASTER_WHISPER_MODELS = [
    "tiny.en",
    "tiny",
    "base.en",
    "base",
    "small.en",
    "small",
    "medium.en",
    "medium",
    "large-v3",
    "distil-small.en",
    "distil-medium.en",
    "distil-large-v3",
]


def list_whisper_models(config_path: Path | str | None = None) -> list[dict[str, str]]:
    """Discover whisper.cpp ``*.bin`` model files near the configured model path.

    Scans the directory of the current ``whisper.model_path`` plus the default
    ``tools/whisper.cpp/models`` dir, and always includes the currently
    configured path so it round-trips even if not on disk.
    """
    cfg = current_app_config(config_path)
    model_path = Path(cfg.whisper.model_path)
    found: dict[str, str] = {}
    # Determine a project-relative models dir based on config file location
    config_file = Path(config_path) if config_path else resolve_config_path()
    project_models = config_file.parent / "tools" / "whisper.cpp" / "models"
    for directory in (model_path.parent, project_models, Path("tools/whisper.cpp/models")):
        try:
            if directory.is_dir():
                for f in sorted(directory.glob("*.bin")):
                    found[str(f.resolve())] = f.name
        except OSError:
            continue
    # Ensure the current selection is present even if missing on disk.
    configured = str(model_path)
    if configured and str(model_path.resolve()) not in found:
        found.setdefault(configured, f"{model_path.name} (configured)")
    return [{"path": p, "label": label} for p, label in found.items()]


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


def _get_path(data: dict[str, Any], path: tuple[str, ...], default: Any = None) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _set_path(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cur: dict[str, Any] = data
    for key in path[:-1]:
        next_value = cur.get(key)
        if not isinstance(next_value, dict):
            next_value = {}
            cur[key] = next_value
        cur = next_value
    cur[path[-1]] = value


def redact_config_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy suitable for UI/API responses with provider keys redacted."""
    redacted = deepcopy(data)
    for path in SECRET_PATHS:
        value = _get_path(redacted, path, "")
        if isinstance(value, str) and value:
            _set_path(redacted, path, REDACTED)
    return redacted


def merge_redacted_secrets(
    posted: dict[str, Any],
    existing: dict[str, Any],
) -> dict[str, Any]:
    """Preserve existing secrets when the UI posts the redacted sentinel."""
    merged = deepcopy(posted)
    for path in SECRET_PATHS:
        if _get_path(merged, path) == REDACTED:
            _set_path(merged, path, _get_path(existing, path, ""))
    return merged


def sanitize_secret_values(message: str, *configs: dict[str, Any]) -> str:
    """Remove provider key values from an error string before returning it."""
    safe = str(message)
    values: set[str] = set()
    for config in configs:
        for path in SECRET_PATHS:
            value = _get_path(config, path, "")
            if isinstance(value, str) and value and value != REDACTED:
                values.add(value)
    for value in sorted(values, key=len, reverse=True):
        safe = safe.replace(value, "[redacted]")
    return safe


def current_app_config(path: Path | str | None = None) -> AppConfig:
    """The on-disk config as an AppConfig, *without* strict validation.

    Used to display the current state and to compare against a save, so an
    already-invalid config (e.g. whisper not built yet) doesn't block the UI.
    """
    return build_config(_read_raw(path), validate=False)


def load_config_dict(path: Path | str | None = None, *, redact: bool = True) -> dict[str, Any]:
    """Load the effective config (defaults + file) as a TOML-shaped nested dict.

    The shape matches what ``save_config_dict`` writes and what the loader
    expects, so the UI can round-trip the whole object (GET then PUT) without
    dropping fields such as per-language vocabulary. Returns defaults if no
    file exists yet. Provider API keys are redacted by default for UI/API use.
    """
    data = to_toml_dict(current_app_config(path))
    return redact_config_secrets(data) if redact else data


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
        "overlay": asdict(config.overlay),
        "streaming": asdict(config.streaming),
    }

    vocab: dict[str, Any] = {"words": list(config.vocabulary.words)}
    for lang, words in config.vocabulary.languages.items():
        vocab[lang] = {"words": list(words)}
    data["vocabulary"] = vocab

    data["snippets"] = {
        "enabled": config.snippets.enabled,
        "bias_recognition": config.snippets.bias_recognition,
        "items": dict(config.snippets.items),
    }
    data["vad"] = asdict(config.vad)
    return data


def validate_config_dict(raw: dict[str, Any]) -> AppConfig:
    """Validate a posted config mapping, raising ValueError if invalid."""
    return build_config(dict(raw))


def save_config_dict(raw: dict[str, Any], path: Path | str | None = None) -> AppConfig:
    """Validate then atomically write the config to TOML. Returns the AppConfig."""
    import tomli_w

    existing = load_config_dict(path, redact=False)
    merged = merge_redacted_secrets(raw, existing)
    config = validate_config_dict(merged)
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
