# Transcription Post-Processing & Newline Cleanup

> **Date**: 2025-04-23
> **Status**: All 8 steps COMPLETE  <!-- Status lifecycle: Exploring → Draft → In Progress → Complete -->
> **Scope**: Add configurable text post-processing between whisper transcription and AI cleanup to remove unwanted newlines and normalize whitespace
> **Last Updated**: 2025-04-23 16:00

---

## Intent

### Problem statement & desired outcomes

Whisper.cpp's server concatenates transcription segments with `\n` characters (one per ~30-second audio segment boundary). These newlines are arbitrary -- they don't correspond to sentence or paragraph boundaries -- and they get pasted verbatim into the target application. The user needs clean, single-line dictation output by default, with configurable post-processing options. Additionally, the cleanup AI system prompt should be updated to handle paragraph formatting when cleanup is enabled.

### Success criteria

- Unwanted newlines from whisper output are collapsed to spaces by default
- A `[postprocess]` config section exists with toggleable options: `collapse_newlines`, `collapse_whitespace`, `trim`, and `trailing`
- The `trailing` option supports `none`, `space`, `newline` (default), `double_newline`, and `tab`
- Post-processing runs before AI cleanup in the pipeline
- The cleanup system prompt instructs the AI to add paragraph breaks when appropriate
- Existing behavior is preserved when all postprocess options are disabled

### Scope boundaries & non-goals

- In scope: configurable text post-processing step, trailing character config, cleanup prompt update
- Out of scope: voice commands (e.g., "new line" -> `\n`), custom vocabulary, dynamic config UI
- Out of scope: changes to whisper.cpp server itself

---

## Context

The whisper.cpp server's `/inference` endpoint (JSON format) builds its `text` field by concatenating all decoded segments with `\n` separators via `output_str()` in `server.cpp`. These segment boundaries are arbitrary (~30s audio chunks) and produce unwanted line breaks when pasted into target applications. The current pipeline (`transcribe -> cleanup -> inject`) has no text normalization step -- only `.strip()` calls at the boundaries.

## Files to modify

| File | Change |
|---|---|
| `src/samwhispers/config.py` | Add `PostprocessConfig` dataclass, wire into `AppConfig`, add validation |
| `src/samwhispers/postprocess.py` | **New file** -- `TextPostprocessor` class with configurable transforms |
| `src/samwhispers/app.py` | Import and wire postprocessor into `_process_recording()` pipeline |
| `src/samwhispers/cleanup.py` | Update `_SYSTEM_PROMPT` constant |
| `config.toml` | Add `[postprocess]` section with commented defaults |
| `tests/test_postprocess.py` | **New file** -- unit tests for `TextPostprocessor` |
| `tests/test_config.py` | Add postprocess default assertions and trailing validation test |
| `tests/test_app.py` | Update `_make_app()` to mock postprocessor; update pipeline assertions |
| `tests/test_integration.py` | Update pipeline assertions to account for trailing newline |

## External Dependencies

None -- code-only change.

## Rollout / Migration / Cleanup

None -- additive feature with sensible defaults. Existing configs without a `[postprocess]` section will get the default values (all transforms enabled, trailing newline).

**Note on cleanup prompt change**: updating `_SYSTEM_PROMPT` in `cleanup.py` is a behavior change for users who already have cleanup enabled. The AI may now add paragraph breaks where it previously did not. This is intentional and aligns with the feature goal, but should be noted in commit message.

## Step-by-step

### 1. Add `PostprocessConfig` to `config.py`

Add the trailing-value map and dataclass after `InjectConfig` (line 169):

```python
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
    collapse_whitespace: bool = True
    trim: bool = True
    trailing: str = "newline"
```

<!-- resolves review finding #4: single source of truth for trailing options -->

`_TRAILING_MAP` lives in `config.py` and `_VALID_TRAILING` is derived from its keys. `postprocess.py` imports `_TRAILING_MAP` from here.

Update `AppConfig` (line 174) to include the new field:

```python
@dataclass
class AppConfig:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    postprocess: PostprocessConfig = field(default_factory=PostprocessConfig)
    inject: InjectConfig = field(default_factory=InjectConfig)
```

Add validation in `_validate()` (after the cleanup validation block, around line 230):

```python
if config.postprocess.trailing not in _VALID_TRAILING:
    raise ValueError(
        f"Invalid postprocess.trailing {config.postprocess.trailing!r}, "
        f"must be one of {_VALID_TRAILING}"
    )
```

Wire into `load_config()` (around line 310) alongside the other config sections:

```python
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
    postprocess=PostprocessConfig(**d.get("postprocess", {})),
    inject=InjectConfig(**d.get("inject", {})),
)
```

### 2. Create `src/samwhispers/postprocess.py`

New module with two methods: `normalize()` (runs before cleanup) and `finalize()` (runs after cleanup).

**Design note**: normalization (collapse newlines, whitespace, trim) must run before AI cleanup so the AI receives clean input. The trailing character must be appended after cleanup so it's always present in the final output regardless of whether cleanup is enabled or what the AI returns.

```python
"""Text post-processing between transcription and cleanup."""

from __future__ import annotations

import logging
import re

from samwhispers.config import PostprocessConfig, _TRAILING_MAP

log = logging.getLogger("samwhispers")


class TextPostprocessor:
    """Apply configurable text transformations to raw transcription output."""

    def __init__(self, config: PostprocessConfig) -> None:
        self._config = config

    def normalize(self, text: str) -> str:
        """Collapse newlines, whitespace, and trim. Run before cleanup."""
        if self._config.collapse_newlines:
            text = text.replace("\n", " ")

        if self._config.collapse_whitespace:
            text = re.sub(r" {2,}", " ", text)

        if self._config.trim:
            text = text.strip()

        return text

    def finalize(self, text: str) -> str:
        """Append trailing character. Run after cleanup."""
        if not text:
            return text

        trailing = _TRAILING_MAP[self._config.trailing]
        if trailing:
            text = text + trailing

        return text
```

<!-- resolves review finding #7: finalize() guards against empty strings -->

### 3. Wire postprocessor into `app.py`

Add import at the top (with the other samwhispers imports):

```python
from samwhispers.postprocess import TextPostprocessor
```

Initialize in `__init__` (after `self.cleanup = CleanupProvider(config.cleanup)`):

```python
self.postprocessor = TextPostprocessor(config.postprocess)
```

Update `_process_recording()` to call `normalize()` before cleanup and `finalize()` after:

```python
    def _process_recording(self, wav_bytes: bytes) -> None:
        import time

        min_size = min_wav_size(self.config.audio.sample_rate)
        if len(wav_bytes) < min_size:
            log.warning(
                "Recording too short (%d bytes, min=%d), skipping", len(wav_bytes), min_size
            )
            return

        duration = (len(wav_bytes) - 44) / (self.config.audio.sample_rate * 2)
        log.info("Transcribing (%.1fs, %d bytes)...", duration, len(wav_bytes))

        t0 = time.monotonic()
        text = self.whisper.transcribe(wav_bytes)
        transcribe_ms = (time.monotonic() - t0) * 1000
        log.info("Transcription took %.0fms", transcribe_ms)

        if not text.strip():
            log.warning("Empty transcription, skipping")
            return

        text = self.postprocessor.normalize(text)

        t0 = time.monotonic()
        text = self.cleanup.cleanup(text)
        cleanup_ms = (time.monotonic() - t0) * 1000
        if self.config.cleanup.enabled:
            log.info("Cleanup took %.0fms", cleanup_ms)

        text = self.postprocessor.finalize(text)

        log.info("Result: %s", text)

        self.hotkey_listener.suppress()
        try:
            self.injector.inject(text)
        finally:
            self.hotkey_listener.resume()
        log.info(
            "Done (total pipeline: transcribe=%.0fms, cleanup=%.0fms)", transcribe_ms, cleanup_ms
        )
```

### 4. Update cleanup system prompt in `cleanup.py`

Replace the `_SYSTEM_PROMPT` constant (lines 13-16):

```python
_SYSTEM_PROMPT = (
    "You are a text cleanup assistant. Fix grammar, punctuation, and capitalization "
    "in the following dictated text. When appropriate for readability, add paragraph "
    "breaks. Return only the corrected text, nothing else."
)
```

<!-- resolves review finding #5: noted as behavior change in Rollout section -->

### 5. Add `[postprocess]` section to `config.toml`

Add after `[cleanup.anthropic]` and before `[inject]`:

```toml
[postprocess]
collapse_newlines = true     # Replace \n from whisper segments with spaces
collapse_whitespace = true   # Collapse multiple spaces into one
trim = true                  # Strip leading/trailing whitespace
trailing = "newline"         # Append after text: "none", "space", "newline", "double_newline", "tab"
```

### 6. Update existing tests

<!-- resolves review finding #2: existing tests will break -->

**`tests/test_app.py`**: Update `_make_app()` to patch `TextPostprocessor`:

```python
def _make_app() -> SamWhispers:
    config = AppConfig()
    config.whisper.managed = False
    with (
        patch("samwhispers.app.AudioRecorder") as mock_rec,
        patch("samwhispers.app.WhisperClient") as mock_wc,
        patch("samwhispers.app.CleanupProvider") as mock_cp,
        patch("samwhispers.wsl.is_wsl", return_value=False),
    ):
        app = SamWhispers(config)
        app.recorder = mock_rec.return_value
        app.whisper = mock_wc.return_value
        app.cleanup = mock_cp.return_value
    app.injector = MagicMock()
    app.hotkey_listener = MagicMock()
    return app
```

No patch needed for `TextPostprocessor` -- it's a pure-logic class that works with default config. But `test_process_recording_full_pipeline` must update its assertion since `finalize()` appends `\n`:

```python
# Before:
app.injector.inject.assert_called_once_with("Hello, world.")
# After:
app.injector.inject.assert_called_once_with("Hello, world.\n")
```

And `cleanup.cleanup` is now called with normalized text (`.strip()` from whisper + `normalize()`):

```python
# Before:
app.cleanup.cleanup.assert_called_once_with("hello world")
# After (normalize strips and collapses, whisper already strips):
app.cleanup.cleanup.assert_called_once_with("hello world")
# ^ This stays the same since "hello world" has no newlines to collapse
```

**`tests/test_integration.py`**: Update assertions for trailing newline:

```python
# test_full_pipeline_wav_to_text:
app.injector.inject.assert_called_once_with("Hello, world.\n")

# test_pipeline_cleanup_disabled:
app.injector.inject.assert_called_once_with("hello world\n")

# test_e2e_hotkey_record_transcribe_inject:
app.injector.inject.assert_called_once_with("hello from e2e\n")
```

**`tests/test_config.py`**: Add to `test_defaults`:

```python
assert config.postprocess.collapse_newlines is True
assert config.postprocess.collapse_whitespace is True
assert config.postprocess.trim is True
assert config.postprocess.trailing == "newline"
```

Add new test:

```python
def test_invalid_trailing_raises(tmp_path: Path) -> None:
    """Invalid postprocess.trailing raises ValueError."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[postprocess]\ntrailing = "invalid"\n[whisper]\nmanaged = false\n')
    with pytest.raises(ValueError, match="Invalid postprocess.trailing"):
        load_config(cfg)
```

### 7. Create `tests/test_postprocess.py`

<!-- resolves review finding #1: no test file planned -->

```python
"""Tests for text post-processing module."""

from __future__ import annotations

from samwhispers.config import PostprocessConfig
from samwhispers.postprocess import TextPostprocessor


def _make(
    collapse_newlines: bool = True,
    collapse_whitespace: bool = True,
    trim: bool = True,
    trailing: str = "newline",
) -> TextPostprocessor:
    return TextPostprocessor(
        PostprocessConfig(
            collapse_newlines=collapse_newlines,
            collapse_whitespace=collapse_whitespace,
            trim=trim,
            trailing=trailing,
        )
    )


def test_collapse_newlines() -> None:
    pp = _make()
    assert pp.normalize("hello\nworld") == "hello world"


def test_collapse_multiple_newlines() -> None:
    pp = _make()
    assert pp.normalize("hello\n\nworld") == "hello world"


def test_collapse_whitespace() -> None:
    pp = _make(collapse_newlines=False)
    assert pp.normalize("hello   world") == "hello world"


def test_trim() -> None:
    pp = _make(collapse_newlines=False, collapse_whitespace=False)
    assert pp.normalize("  hello  ") == "hello"


def test_all_disabled_passthrough() -> None:
    pp = _make(collapse_newlines=False, collapse_whitespace=False, trim=False)
    assert pp.normalize("  hello\n  world  ") == "  hello\n  world  "


def test_trailing_newline() -> None:
    pp = _make(trailing="newline")
    assert pp.finalize("hello") == "hello\n"


def test_trailing_space() -> None:
    pp = _make(trailing="space")
    assert pp.finalize("hello") == "hello "


def test_trailing_none() -> None:
    pp = _make(trailing="none")
    assert pp.finalize("hello") == "hello"


def test_trailing_double_newline() -> None:
    pp = _make(trailing="double_newline")
    assert pp.finalize("hello") == "hello\n\n"


def test_trailing_tab() -> None:
    pp = _make(trailing="tab")
    assert pp.finalize("hello") == "hello\t"


def test_finalize_empty_string() -> None:
    pp = _make(trailing="newline")
    assert pp.finalize("") == ""


def test_normalize_empty_string() -> None:
    pp = _make()
    assert pp.normalize("") == ""


def test_full_pipeline_normalize_then_finalize() -> None:
    pp = _make()
    raw = " The batch max decoding.\nThe RSSI aggregation.\nThe dispenser hardware. "
    normalized = pp.normalize(raw)
    assert normalized == "The batch max decoding. The RSSI aggregation. The dispenser hardware."
    final = pp.finalize(normalized)
    assert final == "The batch max decoding. The RSSI aggregation. The dispenser hardware.\n"


def test_whitespace_only_input() -> None:
    pp = _make()
    assert pp.normalize("   \n\n   ") == ""
    assert pp.finalize("") == ""
```

### 8. Update `docs/ROADMAP.md`

Remove the "many newlines created?" line (this issue is now resolved).

## Verification

1. **Unit tests**: `pytest tests/test_postprocess.py tests/test_config.py -v`
2. **Existing tests**: `pytest tests/ -v` -- all tests should pass with updated assertions
3. **Type check**: `mypy src/samwhispers/postprocess.py src/samwhispers/config.py src/samwhispers/app.py`
4. **Config validation**: Set `trailing = "invalid"` in config.toml, run `python -c "from samwhispers.config import load_config; load_config()"` -- should raise `ValueError`
5. **End-to-end**: Run the daemon, dictate a sentence, verify no spurious newlines in pasted output and trailing newline is present

## Documentation updates

| Document | Update needed | Step |
|---|---|---|
| `config.toml` | `[postprocess]` section added | 5 |
| `docs/ROADMAP.md` | Remove "many newlines created?" item | 8 |

## Review Log

### 2025-04-23 -- Plan Review (via /plan)

8 findings (2 High, 3 Medium, 3 Low). 7 auto-resolved.

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | No `tests/test_postprocess.py` planned | High | Resolved -- added Step 7 with comprehensive unit tests |
| 2 | Existing `test_app.py` and `test_integration.py` will break | High | Resolved -- added Step 6 with specific assertion updates |
| 3 | Plan self-contradicts: Step 2 defines `process()` then abandons it | Medium | Resolved -- Step 2 now shows only the final `normalize()`/`finalize()` design |
| 4 | `_TRAILING_MAP` and `_VALID_TRAILING` are dual sources of truth | Medium | Resolved -- `_TRAILING_MAP` moved to `config.py`, `_VALID_TRAILING` derived from its keys |
| 5 | Cleanup prompt change is a behavior change for existing users | Medium | Resolved -- noted in Rollout section |
| 6 | Line references are inaccurate | Low | Resolved -- corrected key references, used approximate markers |
| 7 | `finalize()` appends trailing to empty strings | Low | Resolved -- added empty-string guard in `finalize()` |
| 8 | `collapse_whitespace` name slightly misleading | Low | Resolved -- renamed to `collapse_spaces` at archival (b25965a) |
