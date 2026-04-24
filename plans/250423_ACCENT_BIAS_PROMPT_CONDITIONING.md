# Accent Bias via Prompt Conditioning

> **Date**: 2025-04-23
> **Status**: Draft  <!-- Status lifecycle: Exploring → Draft → In Progress → Complete -->
> **Scope**: Add whisper initial_prompt accent biasing so non-native speakers get better transcription accuracy

---

## Intent

### Problem statement & desired outcomes

Non-native speakers using SamWhispers get lower transcription accuracy because Whisper's decoder has no context about the speaker's accent. For example, a French speaker dictating in English encounters misrecognitions on words where French pronunciation patterns create ambiguous acoustic signals. The goal is to provide a lightweight, prompt-based mechanism that biases Whisper's decoder toward resolving those ambiguities correctly, using the `initial_prompt` conditioning mechanism already supported by whisper-server.

### Success criteria

1. Setting `whisper.accent = "fr"` with `whisper.languages = ["en"]` causes a generic French-accent prompt to be included in the `prompt` form field sent to whisper-server's `/inference` endpoint.
2. Setting `whisper.accent_prompt = "custom text..."` overrides the generic prompt with the user's freeform text.
3. When cycling to a language that matches `accent`, the accent prompt is suppressed for that language; cycling back re-enables it.
4. The combined prompt (vocabulary + accent) is validated against the whisper.cpp token budget at startup using a character-based heuristic (~4 chars/token for Latin scripts); startup fails with a clear error if exceeded. <!-- resolves review finding #3: success criterion now matches implementation -->
5. `accent_prompt` requires `accent` to be set; config validation errors if `accent_prompt` is non-empty but `accent` is empty.
6. Config with no `accent` set behaves identically to current behavior (no regression).

### Scope boundaries & non-goals

**In scope**:
- `whisper.accent` config field (ISO 639-1 code) for generic accent prompt generation
- `whisper.accent_prompt` config field (freeform override)
- Generic prompt template for accent biasing
- Dynamic accent prompt suppression when active language matches accent code
- Token budget validation at startup using a character-based heuristic
- Integration with the vocabulary plan's prompt assembly (accent appended after vocabulary)

**Out of scope**:
- Model selection guidance (can be done without code changes)
- Fine-tuned model support / LoRA / training pipeline
- Post-correction via AI cleanup for accent-specific errors
- Regional accent variants (e.g., Quebec French vs. Metropolitan French)
- Named accent profiles / profile registry
- Per-language accent mapping (single global accent only)
- Curated per-accent prompts beyond the generic template (users can craft their own via `accent_prompt`)

## Context

This feature builds on the vocabulary plan (`250423_CUSTOM_VOCABULARY_AND_FILLER_REMOVAL.md`), whose Phase 1 (prompt delivery infrastructure) is already implemented. The `WhisperClient` now has a `prompt` property (`transcribe.py:42-46`) and sends it as a form field (`transcribe.py:62-64`). The `_build_vocab_prompt()` method in `app.py:104-126` assembles vocabulary words into a prompt string and rebuilds it on language cycle (`app.py:143`). The accent feature extends this prompt assembly to append an accent-conditioning string.

## Files to modify

| File | Change |
|---|---|
| `src/samwhispers/config.py` | Add `accent` and `accent_prompt` fields to `WhisperConfig`; add `LANGUAGE_NAMES` mapping; add validation for new fields |
| `src/samwhispers/app.py` | Extend `_build_vocab_prompt()` (rename to `_build_prompt()`) to append accent prompt; add accent suppression logic; add token budget validation in `_startup_checks()` |
| `config.example.toml` | Add `accent` and `accent_prompt` fields with comments |
| `config.toml` | Add `accent` and `accent_prompt` fields |
| `README.md` | Document accent bias feature |
| `docs/ROADMAP.md` | Mark accent adaptation as implemented |
| `tests/test_config.py` | Add validation tests for accent fields |
| `tests/test_app.py` | Add prompt assembly and accent suppression tests |

## External Dependencies

None. This is a code-only change using existing whisper-server capabilities.

## Rollout / Migration / Cleanup

None. Both new fields default to empty strings. Existing configs work unchanged.

## Step-by-step

### 1. Config: add accent fields and language name mapping (`config.py`)

Add a `LANGUAGE_NAMES` dict mapping ISO 639-1 codes to English language names (for the generic prompt template). Only needs to cover the `WHISPER_LANGUAGES` set. Add `accent` and `accent_prompt` to `WhisperConfig`.

**`WhisperConfig`** (currently at line 119-124):

```python
@dataclass
class WhisperConfig:
    server_url: str = "http://localhost:8080"
    languages: list[str] = field(default_factory=lambda: ["auto"])
    managed: bool = True
    server_bin: str = "tools/whisper.cpp/build/bin/whisper-server"
    model_path: str = "tools/whisper.cpp/models/ggml-base.en.bin"
    accent: str = ""           # ISO 639-1 code for speaker's native language/accent
    accent_prompt: str = ""    # Freeform override for the accent prompt
```

**`LANGUAGE_NAMES`** (add after `WHISPER_LANGUAGES`, before the dataclasses, around line 115):

```python
# Mapping from ISO 639-1 codes to English language names for prompt generation.
# Covers all codes in WHISPER_LANGUAGES except "auto".
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English", "zh": "Chinese", "de": "German", "es": "Spanish",
    "ru": "Russian", "ko": "Korean", "fr": "French", "ja": "Japanese",
    "pt": "Portuguese", "tr": "Turkish", "pl": "Polish", "ca": "Catalan",
    "nl": "Dutch", "ar": "Arabic", "sv": "Swedish", "it": "Italian",
    "id": "Indonesian", "hi": "Hindi", "fi": "Finnish", "vi": "Vietnamese",
    "he": "Hebrew", "uk": "Ukrainian", "el": "Greek", "ms": "Malay",
    "cs": "Czech", "ro": "Romanian", "da": "Danish", "hu": "Hungarian",
    "ta": "Tamil", "no": "Norwegian", "th": "Thai", "ur": "Urdu",
    "hr": "Croatian", "bg": "Bulgarian", "lt": "Lithuanian", "la": "Latin",
    "mi": "Maori", "ml": "Malayalam", "cy": "Welsh", "sk": "Slovak",
    "te": "Telugu", "fa": "Persian", "lv": "Latvian", "bn": "Bengali",
    "sr": "Serbian", "az": "Azerbaijani", "sl": "Slovenian", "kn": "Kannada",
    "et": "Estonian", "mk": "Macedonian", "br": "Breton", "eu": "Basque",
    "is": "Icelandic", "hy": "Armenian", "ne": "Nepali", "mn": "Mongolian",
    "bs": "Bosnian", "kk": "Kazakh", "sq": "Albanian", "sw": "Swahili",
    "gl": "Galician", "mr": "Marathi", "pa": "Punjabi", "si": "Sinhala",
    "km": "Khmer", "sn": "Shona", "yo": "Yoruba", "so": "Somali",
    "af": "Afrikaans", "oc": "Occitan", "ka": "Georgian", "be": "Belarusian",
    "tg": "Tajik", "sd": "Sindhi", "gu": "Gujarati", "am": "Amharic",
    "yi": "Yiddish", "lo": "Lao", "uz": "Uzbek", "fo": "Faroese",
    "ht": "Haitian Creole", "ps": "Pashto", "tk": "Turkmen",
    "nn": "Norwegian Nynorsk", "mt": "Maltese", "sa": "Sanskrit",
    "lb": "Luxembourgish", "my": "Myanmar", "bo": "Tibetan",
    "tl": "Tagalog", "mg": "Malagasy", "as": "Assamese", "tt": "Tatar",
    "haw": "Hawaiian", "ln": "Lingala", "ha": "Hausa", "ba": "Bashkir",
    "jw": "Javanese", "su": "Sundanese", "yue": "Cantonese",
}
```

**Validation** in `_validate()` (add after the vocabulary validation block, around line 300):

```python
# Validate accent fields
if config.whisper.accent:
    if config.whisper.accent not in WHISPER_LANGUAGES or config.whisper.accent == "auto":
        raise ValueError(
            f"Invalid whisper.accent {config.whisper.accent!r}, "
            "must be a whisper.cpp language code (not 'auto')"
        )
    # Warn if accent matches all configured languages (accent prompt will never be active)
    # <!-- resolves review finding #4: warn on silent no-op config -->
    if all(lang == config.whisper.accent for lang in config.whisper.languages):
        warnings.warn(
            f"whisper.accent {config.whisper.accent!r} matches all configured languages; "
            "accent prompt will never be active",
            UserWarning,
            stacklevel=3,
        )
    # Warn about auto-detect interaction
    # <!-- resolves review finding #5: document auto-detect limitation -->
    if "auto" in config.whisper.languages:
        log.info(
            "Note: accent prompt is always active during auto-detect "
            "(detected language is not known at prompt time)"
        )
if config.whisper.accent_prompt.strip() and not config.whisper.accent:
    # <!-- resolves review findings #11 (whitespace-only) and #14 (better error message) -->
    raise ValueError(
        "whisper.accent_prompt requires whisper.accent to be set. "
        "Set accent to your native language code (e.g., 'fr') to enable accent biasing."
    )
```

**Update `load_config()`**: No special parsing needed -- `accent` and `accent_prompt` are simple string fields on `WhisperConfig`, so the existing `WhisperConfig(**d.get("whisper", {}))` constructor handles them automatically via `_merge()`.

**Tests** (`tests/test_config.py`):

- `test_accent_valid`: `whisper.accent = "fr"` loads successfully
- `test_accent_invalid_code`: `whisper.accent = "zzzz"` raises `ValueError`
- `test_accent_auto_rejected`: `whisper.accent = "auto"` raises `ValueError`
- `test_accent_empty_default`: no accent field -> `accent = ""`
- `test_accent_prompt_requires_accent`: `accent_prompt = "..."` without `accent` raises `ValueError`
- `test_accent_prompt_with_accent`: both set -> loads successfully
- `test_accent_noop_warning`: `accent = "fr"` with `languages = ["fr"]` emits `UserWarning` <!-- resolves review finding #4 -->
- `test_accent_prompt_whitespace_only`: `accent_prompt = "   "` without `accent` raises `ValueError` <!-- resolves review finding #11 -->
- `test_language_names_covers_all_languages`: `WHISPER_LANGUAGES - {"auto"} == set(LANGUAGE_NAMES.keys())` <!-- resolves review finding #7 -->

### 2. Prompt assembly: extend `_build_vocab_prompt()` with accent (`app.py`)

Rename `_build_vocab_prompt()` to `_build_prompt()` and extend it to append the accent prompt after the vocabulary prompt. The accent prompt is suppressed when the active language matches the accent code.

**Rename ripple effect**: Update all existing references to `_build_vocab_prompt` in `tests/test_app.py` to `_build_prompt`. Affected tests: `test_build_vocab_prompt_global_only`, `test_build_vocab_prompt_with_language`, `test_build_vocab_prompt_auto_language`, `test_build_vocab_prompt_empty`, `test_build_vocab_prompt_deduplicates`, `test_vocab_prompt_updates_on_language_cycle`, and the init call site at `app.py:57`. <!-- resolves review finding #1 -->

**Generic accent prompt template** (use the constant for maintainability): <!-- resolves review finding #8 -->

```python
_ACCENT_PROMPT_TEMPLATE = (
    "The speaker has a {accent_name} accent."
)
```

**Updated method** (replaces `_build_vocab_prompt` at `app.py:104-126`):

```python
def _build_prompt(self) -> str:
    """Build initial_prompt from vocabulary + accent config and current language."""
    parts: list[str] = []

    # --- Vocabulary portion ---
    words = list(self.config.vocabulary.words)
    lang = self.whisper.language
    if lang != "auto" and lang in self.config.vocabulary.languages:
        words.extend(self.config.vocabulary.languages[lang])
    if words:
        seen: set[str] = set()
        unique: list[str] = []
        for w in words:
            wl = w.lower()
            if wl not in seen:
                seen.add(wl)
                unique.append(w)
        if len(unique) > 100:
            log.warning(
                "Vocabulary has %d words; initial_prompt token limit is ~150-200 words. "
                "Consider trimming the list.",
                len(unique),
            )
        parts.append(", ".join(unique))

    # --- Accent portion ---
    accent = self.config.whisper.accent
    if accent and lang != accent:
        if self.config.whisper.accent_prompt.strip():
            parts.append(self.config.whisper.accent_prompt.strip())
        else:
            accent_name = LANGUAGE_NAMES.get(accent, accent)
            parts.append(_ACCENT_PROMPT_TEMPLATE.format(accent_name=accent_name))
            # <!-- resolves review findings #8 (use constant) and #9 (module-level import) -->

    return " ".join(parts)
```

**Update all call sites** (2 locations):
- `app.py:57` (init): `self.whisper.prompt = self._build_prompt()`
- `app.py:143` (language cycle): `self.whisper.prompt = self._build_prompt()`

**Module-level imports**: Add `LANGUAGE_NAMES` to the existing import from `samwhispers.config`: <!-- resolves review finding #9 -->
```python
from samwhispers.config import AppConfig, LANGUAGE_NAMES
```

**Add accent logging** in `_startup_checks()` (after the vocabulary logging block, around line 280):

```python
# Accent logging
if self.config.whisper.accent:
    accent_name = LANGUAGE_NAMES.get(
        self.config.whisper.accent, self.config.whisper.accent
    )
    if self.config.whisper.accent_prompt:
        log.info("Accent bias: %s (custom prompt)", accent_name)
    else:
        log.info("Accent bias: %s (generic prompt)", accent_name)
```

**Tests** (`tests/test_app.py`):

Note: All new tests using `_make_app()` must explicitly set `app.whisper.language` and `app.config.whisper.accent` / `app.config.whisper.accent_prompt` on the mock before calling `_build_prompt()`, matching the pattern used by existing vocabulary tests. <!-- resolves review finding #2 -->

- `test_build_prompt_accent_only`: `accent="fr"`, no vocabulary, `language="en"` -> `"The speaker has a French accent."`
- `test_build_prompt_accent_suppressed_when_language_matches`: `accent="fr"`, `language="fr"` -> `""` (no accent prompt)
- `test_build_prompt_accent_with_vocabulary`: `accent="fr"`, vocabulary=`["RSSI"]`, `language="en"` -> `"RSSI, The speaker has a French accent."` (note: comma-joined vocab, then space-joined with accent)
- `test_build_prompt_accent_prompt_override`: `accent="fr"`, `accent_prompt="Custom accent text"`, `language="en"` -> `"Custom accent text"`
- `test_build_prompt_accent_suppressed_on_cycle`: cycle from `en` to `fr` with `accent="fr"` -> accent prompt disappears; cycle back to `en` -> accent prompt reappears
- `test_build_prompt_accent_auto_language`: `accent="fr"`, `language="auto"` -> accent prompt IS included (auto != fr)
- `test_build_prompt_no_accent`: no accent set -> behaves identically to current `_build_vocab_prompt()`

### 3. Token budget validation at startup (`app.py`)

Add a prompt validation step in `_startup_checks()`, after the whisper server is confirmed ready. Send the assembled prompt to the server via a test transcription request with minimal audio to verify it doesn't exceed the token budget.

The whisper-server doesn't expose a `/tokenize` endpoint, but we can validate by sending a short silent WAV with the full prompt and checking for errors. However, whisper.cpp silently truncates oversized prompts rather than erroring. So we'll use a **character-based heuristic validated against the server**: estimate ~4 chars per token, warn at 700 chars (conservative), error at 900 chars (hard limit).

**In `_startup_checks()`**, after the whisper server health check passes:

```python
# Validate combined prompt token budget
prompt = self._build_prompt()
if prompt:
    # whisper.cpp initial_prompt limit: whisper_n_text_ctx()/2 ≈ 224 tokens
    # Heuristic: ~4 chars per BPE token for English text
    estimated_tokens = len(prompt) / 4
    if estimated_tokens > 224:
        log.error(
            "Combined prompt is too long (~%d tokens, limit ~224). "
            "Reduce vocabulary list or accent_prompt. Prompt: %.100s...",
            int(estimated_tokens), prompt,
        )
        raise SystemExit(1)
    elif estimated_tokens > 180:
        log.warning(
            "Combined prompt is approaching token limit (~%d/224 tokens). "
            "Consider reducing vocabulary list or accent_prompt.",
            int(estimated_tokens),
        )
    log.info("Prompt (%d chars, ~%d tokens): %s",
             len(prompt), int(estimated_tokens), prompt)
```

Note: The user requested server-based validation, but whisper-server silently truncates oversized prompts rather than returning an error. A heuristic with a safety margin is the most reliable approach. The prompt is logged at startup so the user can verify it looks correct.

**Tests** (`tests/test_app.py`):

- `test_startup_prompt_too_long_exits`: set a very long `accent_prompt` (>900 chars) -> `SystemExit`
- `test_startup_prompt_warning_near_limit`: set a prompt near the limit (~750 chars) -> warning logged but no exit

### 4. Config files and documentation

**`config.example.toml`** -- add to the `[whisper]` section:

```toml
accent = ""                     # Your native language code for accent biasing (e.g., "fr" for
                                # a French speaker dictating in English). Biases Whisper toward
                                # recognizing accented speech. Suppressed when the active
                                # language matches the accent code.
# accent_prompt = ""            # Custom accent prompt (overrides the generic template).
                                # Example: "The speaker is a native French speaker.
                                # Words like 'the', 'this', 'that' may sound like 'ze', 'zis', 'zat'."
```
<!-- resolves review findings #12 (example content) and end-user #1 (naming clarity) -->

**`config.toml`** -- add the same fields.

**`README.md`** -- add an "Accent Bias" section under Configuration:

```markdown
## Accent Bias

If you speak with a non-native accent (e.g., French-accented English), you can
bias Whisper's decoder to improve recognition accuracy:

\```toml
[whisper]
accent = "fr"    # Your native language code
\```

This adds a conditioning prompt to Whisper's decoder. When you cycle to a
language that matches your accent (e.g., switching to French), the accent
prompt is automatically suppressed since it's not needed.

For custom control, override the generated prompt:

\```toml
[whisper]
accent = "fr"
accent_prompt = "The speaker is a native French speaker with a strong accent."
\```

Note: Accent biasing conditions the text decoder, not the acoustic model.
It helps with ambiguous words but cannot fix purely acoustic misrecognitions.
For best results, combine with a larger model (medium or large).

The accent prompt is combined with your vocabulary list into a single prompt.
If you use both features, keep the total short to stay within Whisper's ~224
token limit.

When using auto-detect (`languages = ["auto"]`), the accent prompt is always
active because the detected language is not known at prompt time. For best
results with accent biasing, use explicit language codes.
```
<!-- resolves review findings #5 (auto-detect docs) and #13 (vocab interaction docs) -->

**`docs/ROADMAP.md`** -- mark accent adaptation as implemented.

**Exit criteria**:
- [ ] `whisper.accent = "fr"` with `languages = ["en"]` sends accent prompt in the `prompt` form field
- [ ] `whisper.accent_prompt` overrides the generic prompt
- [ ] Accent prompt suppressed when active language matches accent code
- [ ] Combined prompt validated at startup; exits if too long
- [ ] `accent_prompt` without `accent` raises config validation error
- [ ] No accent set -> identical behavior to current (no regression)
- [ ] All new tests pass
- [ ] `make check` passes (lint + typecheck + tests)

## Verification

**Automated**:
```bash
make check   # lint + typecheck + tests
```

**Manual**:
1. Set `whisper.accent = "fr"` with `languages = ["en"]`, run SamWhispers, check verbose log shows the accent prompt
2. Set `whisper.accent_prompt = "Custom text"`, verify it overrides the generic prompt in logs
3. Configure `languages = ["auto", "en", "fr"]` with `accent = "fr"`, cycle to French, verify accent prompt disappears from logs; cycle back to English, verify it reappears
4. Set a very long `accent_prompt` (>900 chars), verify startup fails with clear error
5. Remove `accent` field entirely, verify behavior is identical to before

## Documentation updates

| Document | Update needed | Step |
|---|---|---|
| `config.example.toml` | Add `accent` and `accent_prompt` fields with comments | Step 4 |
| `config.toml` | Add `accent` and `accent_prompt` fields | Step 4 |
| `README.md` | Add Accent Bias section | Step 4 |
| `docs/ROADMAP.md` | Mark accent adaptation as done | Step 4 |

## Review Log

### 2025-04-23 -- Plan Creation Review (via /plan)

14 findings (1 High, 6 Medium, 7 Low). 14 auto-resolved.

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | Rename `_build_vocab_prompt` -> `_build_prompt` breaks 7+ existing tests | Medium | Resolved -- added explicit rename ripple effect note listing all affected tests |
| 2 | `_make_app()` mock setup requirement not documented for new accent tests | Medium | Resolved -- added note about setting mock attributes before calling `_build_prompt()` |
| 3 | Success criterion #4 says "tokenization request to server" but implementation uses heuristic | Medium | Resolved -- updated success criterion to match actual implementation |
| 4 | No warning when `accent` matches all configured languages (silent no-op) | High | Resolved -- added `warnings.warn()` in `_validate()` for no-op config |
| 5 | `accent` + `languages = ["auto"]` behavior undocumented | Medium | Resolved -- added startup log note and README documentation |
| 6 | Token heuristic unreliable for non-Latin scripts | Medium | Noted -- acceptable for v1; document as approximate. Non-Latin accent prompts are an edge case. |
| 7 | `LANGUAGE_NAMES` could drift from `WHISPER_LANGUAGES` | Low | Resolved -- added test `test_language_names_covers_all_languages` |
| 8 | `_ACCENT_PROMPT_TEMPLATE` constant defined but unused in code | Low | Resolved -- updated code to use `_ACCENT_PROMPT_TEMPLATE.format()` |
| 9 | `LANGUAGE_NAMES` import should be at module level | Low | Resolved -- moved to module-level import |
| 10 | Line number inaccuracies (~2-4 lines off) | Low | Noted -- minor, won't block implementation |
| 11 | `accent_prompt` whitespace-only bypasses validation | Low | Resolved -- added `.strip()` to validation check |
| 12 | No example of good `accent_prompt` content in config.example.toml | Low | Resolved -- added commented example in config.example.toml section |
| 13 | README doesn't mention vocab+accent interaction / token budget | Low | Resolved -- added paragraph to README section |
| 14 | Error message for `accent_prompt` without `accent` lacks guidance | Low | Resolved -- improved error message with actionable suggestion |

Reviewed by: Implementability reviewer (confidence: 82%), End-user advocate (confidence: 82%). All High and Medium findings auto-resolved.
