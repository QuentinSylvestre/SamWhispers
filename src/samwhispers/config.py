"""TOML configuration loading and validation."""

from __future__ import annotations

import logging
import os
import tomllib
import warnings
from dataclasses import dataclass, field, fields as dataclass_fields
from pathlib import Path
from typing import Any

log = logging.getLogger("samwhispers")

_VALID_MODES = ("hold", "toggle")
_VALID_PROVIDERS = ("openai", "anthropic")
_VALID_STREAM_ENGINES = ("chunked", "faster_whisper")
_VALID_STREAM_MODES = ("preview", "progressive")

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


# Mapping from ISO 639-1 codes to English language names for prompt generation.
# Covers all codes in WHISPER_LANGUAGES except "auto".
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "zh": "Chinese",
    "de": "German",
    "es": "Spanish",
    "ru": "Russian",
    "ko": "Korean",
    "fr": "French",
    "ja": "Japanese",
    "pt": "Portuguese",
    "tr": "Turkish",
    "pl": "Polish",
    "ca": "Catalan",
    "nl": "Dutch",
    "ar": "Arabic",
    "sv": "Swedish",
    "it": "Italian",
    "id": "Indonesian",
    "hi": "Hindi",
    "fi": "Finnish",
    "vi": "Vietnamese",
    "he": "Hebrew",
    "uk": "Ukrainian",
    "el": "Greek",
    "ms": "Malay",
    "cs": "Czech",
    "ro": "Romanian",
    "da": "Danish",
    "hu": "Hungarian",
    "ta": "Tamil",
    "no": "Norwegian",
    "th": "Thai",
    "ur": "Urdu",
    "hr": "Croatian",
    "bg": "Bulgarian",
    "lt": "Lithuanian",
    "la": "Latin",
    "mi": "Maori",
    "ml": "Malayalam",
    "cy": "Welsh",
    "sk": "Slovak",
    "te": "Telugu",
    "fa": "Persian",
    "lv": "Latvian",
    "bn": "Bengali",
    "sr": "Serbian",
    "az": "Azerbaijani",
    "sl": "Slovenian",
    "kn": "Kannada",
    "et": "Estonian",
    "mk": "Macedonian",
    "br": "Breton",
    "eu": "Basque",
    "is": "Icelandic",
    "hy": "Armenian",
    "ne": "Nepali",
    "mn": "Mongolian",
    "bs": "Bosnian",
    "kk": "Kazakh",
    "sq": "Albanian",
    "sw": "Swahili",
    "gl": "Galician",
    "mr": "Marathi",
    "pa": "Punjabi",
    "si": "Sinhala",
    "km": "Khmer",
    "sn": "Shona",
    "yo": "Yoruba",
    "so": "Somali",
    "af": "Afrikaans",
    "oc": "Occitan",
    "ka": "Georgian",
    "be": "Belarusian",
    "tg": "Tajik",
    "sd": "Sindhi",
    "gu": "Gujarati",
    "am": "Amharic",
    "yi": "Yiddish",
    "lo": "Lao",
    "uz": "Uzbek",
    "fo": "Faroese",
    "ht": "Haitian Creole",
    "ps": "Pashto",
    "tk": "Turkmen",
    "nn": "Norwegian Nynorsk",
    "mt": "Maltese",
    "sa": "Sanskrit",
    "lb": "Luxembourgish",
    "my": "Myanmar",
    "bo": "Tibetan",
    "tl": "Tagalog",
    "mg": "Malagasy",
    "as": "Assamese",
    "tt": "Tatar",
    "haw": "Hawaiian",
    "ln": "Lingala",
    "ha": "Hausa",
    "ba": "Bashkir",
    "jw": "Javanese",
    "su": "Sundanese",
    "yue": "Cantonese",
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
    accent: str = ""  # ISO 639-1 code for speaker's native language/accent
    accent_prompt: str = ""  # Freeform override for the accent prompt


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


_TRAILING_MAP = {
    "none": "",
    "space": " ",
    "newline": "\n",
    "double_newline": "\n\n",
    "tab": "\t",
}

_VALID_TRAILING = tuple(_TRAILING_MAP.keys())


@dataclass
class PostprocessConfig:
    collapse_newlines: bool = True
    collapse_spaces: bool = True
    trim: bool = True
    trailing: str = "newline"


@dataclass
class VocabularyConfig:
    words: list[str] = field(default_factory=list)
    languages: dict[str, list[str]] = field(default_factory=dict)


BUILTIN_FILLERS: dict[str, list[str]] = {
    "en": ["um", "uh", "hmm", "mm", "mhm", "mmm", "ah", "oh", "er"],
    "fr": ["euh", "bah", "beh", "ben", "hein", "mmh", "mh", "pfff"],
}


@dataclass
class FillerConfig:
    enabled: bool = True
    words: list[str] = field(default_factory=list)
    use_builtins: bool = True


@dataclass
class HistoryConfig:
    enabled: bool = True
    max_entries: int = 1000  # retention cap; 0 = unlimited


@dataclass
class TranslationConfig:
    # Translates the dictated text into target_language before injecting it,
    # using the AI provider/keys configured in [cleanup].
    enabled: bool = False
    target_language: str = "en"


@dataclass
class OverlayConfig:
    # Floating on-screen indicator: animated bars while recording, a spinner
    # while transcribing. Needs a display; silently disabled without one.
    enabled: bool = True


@dataclass
class StreamingConfig:
    # Continuous (streaming) transcription instead of batch-at-the-end.
    #   engine:      "chunked" (re-decode via whisper.cpp) | "faster_whisper"
    #   output_mode: "preview" (A: live preview, inject final paragraph) |
    #                "progressive" (B: inject stable words as they commit)
    enabled: bool = False
    engine: str = "chunked"
    output_mode: str = "preview"
    interval_seconds: float = 0.8  # how often to re-decode while speaking
    model: str = "base"  # faster-whisper model name or path
    compute_type: str = "int8"  # faster-whisper compute type
    window_seconds: float = 30.0  # max audio window per tick (caps CPU cost)


@dataclass
class VadConfig:
    enabled: bool = False
    # Server-side (whisper.cpp --vad flags)
    model_path: str = ""
    threshold: float = 0.5
    min_speech_duration_ms: int = 250
    min_silence_duration_ms: int = 100
    max_speech_duration_s: float = 0.0  # 0 = unlimited
    speech_pad_ms: int = 30
    samples_overlap: float = 0.1
    # Client-side (auto-stop on silence, toggle mode only)
    silence_threshold: float = 0.01
    silence_duration: float = 10.0


@dataclass
class SnippetConfig:
    items: dict[str, str] = field(default_factory=dict)  # trigger -> expansion
    bias_recognition: bool = True  # add triggers to vocabulary prompt
    enabled: bool = True  # master toggle


@dataclass
class AppConfig:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    postprocess: PostprocessConfig = field(default_factory=PostprocessConfig)
    inject: InjectConfig = field(default_factory=InjectConfig)
    vocabulary: VocabularyConfig = field(default_factory=VocabularyConfig)
    filler: FillerConfig = field(default_factory=FillerConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    translation: TranslationConfig = field(default_factory=TranslationConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    streaming: StreamingConfig = field(default_factory=StreamingConfig)
    snippets: SnippetConfig = field(default_factory=SnippetConfig)
    vad: VadConfig = field(default_factory=VadConfig)


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
    # Validate server_url scheme and port
    from urllib.parse import urlparse

    parsed_url = urlparse(config.whisper.server_url)
    if parsed_url.scheme not in ("http", "https"):
        raise ValueError(
            f"Invalid whisper.server_url scheme {parsed_url.scheme!r}, must be 'http' or 'https'"
        )
    try:
        port = parsed_url.port
    except ValueError:
        raise ValueError(
            f"Invalid whisper.server_url port in {config.whisper.server_url!r}, "
            "must be between 1 and 65535"
        ) from None
    if port is not None and not (1 <= port <= 65535):
        raise ValueError(f"Invalid whisper.server_url port {port}, must be between 1 and 65535")

    if config.whisper.managed:
        from samwhispers.server import _resolve_server_bin

        bin_path = Path(_resolve_server_bin(config.whisper.server_bin))
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
    if config.postprocess.trailing not in _VALID_TRAILING:
        raise ValueError(
            f"Invalid postprocess.trailing {config.postprocess.trailing!r}, "
            f"must be one of {_VALID_TRAILING}"
        )

    if config.history.max_entries < 0:
        raise ValueError(
            f"Invalid history.max_entries {config.history.max_entries}, must be >= 0 (0 = unlimited)"
        )

    if config.streaming.engine not in _VALID_STREAM_ENGINES:
        raise ValueError(
            f"Invalid streaming.engine {config.streaming.engine!r}, "
            f"must be one of {_VALID_STREAM_ENGINES}"
        )
    if config.streaming.output_mode not in _VALID_STREAM_MODES:
        raise ValueError(
            f"Invalid streaming.output_mode {config.streaming.output_mode!r}, "
            f"must be one of {_VALID_STREAM_MODES}"
        )
    if config.streaming.interval_seconds <= 0:
        raise ValueError("streaming.interval_seconds must be > 0")
    if config.streaming.enabled and config.streaming.engine == "faster_whisper":
        import importlib.util

        if importlib.util.find_spec("faster_whisper") is None:
            warnings.warn(
                "streaming.engine is 'faster_whisper' but the package is not installed. "
                "Install it with: pip install samwhispers[faster-whisper]",
                UserWarning,
                stacklevel=3,
            )

    if config.translation.target_language not in WHISPER_LANGUAGES or (
        config.translation.target_language == "auto"
    ):
        raise ValueError(
            f"Invalid translation.target_language {config.translation.target_language!r}, "
            "must be a language code (not 'auto')"
        )
    if config.translation.enabled:
        key = (
            config.cleanup.openai.api_key
            if config.cleanup.provider == "openai"
            else config.cleanup.anthropic.api_key
        )
        if not key:
            warnings.warn(
                f"Translation enabled but {config.cleanup.provider} API key is empty",
                UserWarning,
                stacklevel=3,
            )

    # Validate vocabulary language codes
    for lang in config.vocabulary.languages:
        if lang not in WHISPER_LANGUAGES or lang == "auto":
            raise ValueError(
                f"Invalid vocabulary language {lang!r}, "
                "must be a whisper.cpp language code (not 'auto')"
            )

    # Validate accent fields
    if config.whisper.accent:
        if config.whisper.accent not in WHISPER_LANGUAGES or config.whisper.accent == "auto":
            raise ValueError(
                f"Invalid whisper.accent {config.whisper.accent!r}, "
                "must be a whisper.cpp language code (not 'auto'). "
                "Common codes: en, fr, de, es, zh, ja, ko, pt, it, ru."
            )
        # Warn if accent matches all configured languages (accent prompt will never be active)
        if all(lang == config.whisper.accent for lang in config.whisper.languages):
            warnings.warn(
                f"whisper.accent {config.whisper.accent!r} matches all configured languages; "
                "accent prompt will never be active",
                UserWarning,
                stacklevel=3,
            )
        # Note about auto-detect interaction
        if "auto" in config.whisper.languages:
            log.info(
                "Note: accent prompt is always active during auto-detect "
                "(detected language is not known at prompt time)"
            )
    if config.whisper.accent_prompt.strip() and not config.whisper.accent:
        raise ValueError(
            "whisper.accent_prompt requires whisper.accent to be set. "
            "Set accent to your native language code (e.g., 'fr') to enable accent biasing."
        )

    # Validate snippets
    for trigger, expansion in config.snippets.items.items():
        if not trigger.strip():
            raise ValueError("Snippet trigger must not be empty or whitespace-only")
        if not expansion:
            raise ValueError(f"Snippet expansion for trigger {trigger!r} must not be empty")

    # Validate VAD
    if config.vad.enabled and config.vad.model_path:
        if not Path(config.vad.model_path).is_file():
            raise ValueError(
                f"vad.model_path not found: {Path(config.vad.model_path).resolve()}. "
                "Download the VAD model with `samwhispers-setup` or disable VAD."
            )
    elif config.vad.enabled and not config.vad.model_path:
        warnings.warn(
            "VAD enabled but model_path is empty — server-side VAD will not be active. "
            "Download the model with `samwhispers-setup` or set vad.model_path.",
            UserWarning,
            stacklevel=3,
        )
    if not (0.0 <= config.vad.threshold <= 1.0):
        raise ValueError(f"vad.threshold must be between 0.0 and 1.0, got {config.vad.threshold}")
    if not (0.0 <= config.vad.silence_threshold <= 1.0):
        raise ValueError(
            f"vad.silence_threshold must be between 0.0 and 1.0, got {config.vad.silence_threshold}"
        )
    if config.vad.silence_duration <= 0:
        raise ValueError(f"vad.silence_duration must be > 0, got {config.vad.silence_duration}")


def load_config(path: Path | str | None = None) -> AppConfig:
    """Load TOML config, merge with defaults, validate."""
    raw: dict[str, Any] = {}
    if path is not None:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Config file not found: {p}")
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
        log.info("Loaded config from %s", p)
    else:
        found = find_config()
        if found:
            raw = tomllib.loads(found.read_text(encoding="utf-8"))
            log.info("Loaded config from %s", found)
        else:
            log.info("No config file found, using defaults")

    return build_config(raw)


def _filter_fields(cls: type, raw: dict[str, Any]) -> dict[str, Any]:
    """Filter a dict to only keys that match dataclass field names."""
    valid = {f.name for f in dataclass_fields(cls)}
    unknown = set(raw) - valid
    if unknown:
        log.warning("Ignoring unknown config keys for %s: %s", cls.__name__, sorted(unknown))
    return {k: v for k, v in raw.items() if k in valid}


def build_config(raw: dict[str, Any], validate: bool = True) -> AppConfig:
    """Merge a raw config mapping over defaults and construct an AppConfig.

    Shared by ``load_config`` (file-based) and the web UI (which validates a
    posted config mapping through exactly the same path). Pass ``validate=False``
    to skip filesystem/value checks -- the UI uses this to *display* a config
    even when, e.g., the whisper binary hasn't been built yet.
    """
    # Build config from defaults merged with file values
    defaults = AppConfig()

    # Backward compat: whisper.language (str) -> whisper.languages (list)
    whisper_raw = raw.get("whisper", {})
    if "language" in whisper_raw and "languages" not in whisper_raw:
        whisper_raw["languages"] = [whisper_raw.pop("language")]
    elif "language" in whisper_raw and "languages" in whisper_raw:
        whisper_raw.pop("language")  # languages takes precedence

    d = _merge(_to_dict(defaults), raw)

    # --- Vocabulary: manual parsing (sub-tables like [vocabulary.en] mix with scalar keys) ---
    vocab_raw = d.get("vocabulary", {})
    vocab_words = vocab_raw.get("words", [])
    vocab_langs: dict[str, list[str]] = {}
    for k, v in vocab_raw.items():
        if k in ("words", "languages"):
            continue  # skip the top-level keys, only process language sub-tables
        if isinstance(v, dict) and "words" in v:
            vocab_langs[k] = v["words"]

    # --- Filler: manual field extraction (safe against unexpected TOML keys) ---
    filler_raw = d.get("filler", {})
    filler_cfg = FillerConfig(
        enabled=filler_raw.get("enabled", True),
        words=filler_raw.get("words", []),
        use_builtins=filler_raw.get("use_builtins", True),
    )

    # --- Snippets: nested [snippets.items] sub-table ---
    snippets_raw = d.get("snippets", {})
    items_raw = snippets_raw.get("items", {})
    snippets_cfg = SnippetConfig(
        items=dict(items_raw),
        bias_recognition=snippets_raw.get("bias_recognition", True),
        enabled=snippets_raw.get("enabled", True),
    )

    # --- VAD: manual field extraction ---
    vad_raw = d.get("vad", {})
    vad_cfg = VadConfig(**_filter_fields(VadConfig, vad_raw))

    config = AppConfig(
        hotkey=HotkeyConfig(**_filter_fields(HotkeyConfig, d.get("hotkey", {}))),
        whisper=WhisperConfig(**_filter_fields(WhisperConfig, d.get("whisper", {}))),
        audio=AudioConfig(**_filter_fields(AudioConfig, d.get("audio", {}))),
        cleanup=CleanupConfig(
            enabled=d.get("cleanup", {}).get("enabled", False),
            provider=d.get("cleanup", {}).get("provider", "openai"),
            openai=OpenAIConfig(**_filter_fields(OpenAIConfig, d.get("cleanup", {}).get("openai", {}))),
            anthropic=AnthropicConfig(**_filter_fields(AnthropicConfig, d.get("cleanup", {}).get("anthropic", {}))),
        ),
        postprocess=PostprocessConfig(**_filter_fields(PostprocessConfig, d.get("postprocess", {}))),
        inject=InjectConfig(**_filter_fields(InjectConfig, d.get("inject", {}))),
        vocabulary=VocabularyConfig(words=vocab_words, languages=vocab_langs),
        filler=filler_cfg,
        history=HistoryConfig(**_filter_fields(HistoryConfig, d.get("history", {}))),
        translation=TranslationConfig(**_filter_fields(TranslationConfig, d.get("translation", {}))),
        overlay=OverlayConfig(**_filter_fields(OverlayConfig, d.get("overlay", {}))),
        streaming=StreamingConfig(**_filter_fields(StreamingConfig, d.get("streaming", {}))),
        snippets=snippets_cfg,
        vad=vad_cfg,
    )
    if validate:
        _validate(config)
    return config


def _to_dict(obj: Any) -> dict[str, Any]:
    """Convert nested dataclass to dict."""
    from dataclasses import asdict

    result: dict[str, Any] = asdict(obj)
    return result
