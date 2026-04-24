# Custom Vocabulary Support & Filler Word Removal

> **Date**: 2025-04-23
> **Status**: In Progress  <!-- Status lifecycle: Exploring → Draft → In Progress → Complete -->
> **Scope**: Add whisper initial_prompt vocabulary biasing and regex-based filler word removal to the transcription pipeline
> **Estimated effort**: 1-2 days

---

## Intent

### Problem statement & desired outcomes

SamWhispers users frequently encounter two transcription quality issues that require manual correction: (1) uncommon words (proper nouns, technical terms, project names) are consistently misrecognized by Whisper because the decoder has no context about the user's domain, and (2) filler words (especially French ones like "euh", "bah", "mmmh") pass through into the transcribed text and must be manually deleted. Both problems should be solvable without relying on the AI cleanup feature, which is an optional cloud dependency.

### Success criteria

- Users can define a vocabulary list in `config.toml` (global + per-language) that biases Whisper toward recognizing those words, via the `initial_prompt` mechanism
- Users can enable/disable filler word removal, which ships with built-in defaults for English and French (unambiguous interjections only) and supports user-defined custom patterns
- Filler removal handles elongated variants automatically (e.g., `euh` catches `euuuuuh`)
- Filler removal is regex-based post-processing in the existing `TextPostprocessor` pipeline, enabled by default, configurable per-config
- Both features work without AI cleanup enabled
- Per-language vocabulary merges with global vocabulary when a specific language is active; only global vocabulary is sent when language is "auto"

### Scope boundaries & non-goals

**In scope**:
- `initial_prompt`-based vocabulary biasing via whisper-server's `prompt` form field
- Inline vocabulary config in `config.toml` (global + per-language sections)
- Regex-based filler word removal in `postprocess.py` with built-in defaults and user-configurable patterns
- Auto-generation of elongated-variant regex from simple word entries

**Out of scope**:
- Vocabulary file support (plain text file referenced from config) -- deferred to later if inline list feels cramped
- Vocabulary profiles / hotkey switching between vocab sets -- future enhancement
- Full `initial_prompt` / pre-prompt control (style hints, context) -- interesting but needs separate design
- Semantic/LLM-based filler detection -- requires AI cleanup, which is explicitly not the goal
- Post-correction dictionary (mapping misrecognitions to correct words) -- deferred

---

## 1) Current State

<!-- Line references verified against current source 2025-04-23 -->

**Transcription pipeline** (`app.py:190-231`):
```
whisper.transcribe(wav) → postprocessor.normalize(text) → cleanup.cleanup(text) → postprocessor.finalize(text) → injector.inject(text)
```

**WhisperClient** (`transcribe.py:46-55`): POST to `/inference` sends three form fields -- `temperature`, `response_format`, `language`. No `prompt` field is sent. The whisper-server accepts a `prompt` form field (confirmed in whisper.cpp `server.cpp`) that maps to `wparams.initial_prompt`.

**TextPostprocessor** (`postprocess.py:13-40`): Two-phase processing:
- `normalize()` (line 19): collapse_newlines → collapse_spaces → trim. No word-level filtering.
- `finalize()` (line 31): appends trailing character.

**Config** (`config.py`):
- `PostprocessConfig` (line 185-189): `collapse_newlines`, `collapse_spaces`, `trim`, `trailing`.
- `AppConfig` (line 193-199): top-level dataclass aggregating all config sections. No `vocabulary` or `filler` sections exist.
- `load_config()` (line 304-349): TOML loading with `_merge()` for defaults, manual construction of nested dataclasses for `CleanupConfig`. New sections need the same treatment.
- `_validate()` (line 225-301): validates all config sections. New sections need validation added here.

**WhisperClient constructor** (`transcribe.py:22-28`): Takes `server_url` and `language`. No vocabulary/prompt parameter.

**SamWhispers app** (`app.py:48-54`): Creates `WhisperClient` with `server_url` and initial language. Creates `TextPostprocessor` with `PostprocessConfig`. No vocabulary is passed to either.

## 2) Goal

Add two independent features to the transcription pipeline: (1) send a vocabulary-derived `prompt` field to whisper-server's `/inference` endpoint to bias recognition toward user-defined words, and (2) apply configurable regex-based filler word removal in the post-processing step, with built-in defaults for English and French and support for elongated variants.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Vocabulary delivery mechanism | whisper-server `prompt` form field (maps to `initial_prompt`) | Post-correction dictionary; fine-tuned model | `initial_prompt` is built into whisper.cpp, requires no model changes, and covers the main use case. Post-correction deferred. |
| Vocabulary config structure | Inline `[vocabulary]` section in config.toml with global `words` + per-language `[vocabulary.xx]` sub-sections | Separate file; vocabulary profiles with hotkey switching | Inline is simplest; token limit (~224 tokens) naturally caps list size. File support and profiles deferred. |
| Prompt construction | Join vocabulary words with commas into a single prompt string | Sentence-style prompt; raw pass-through | Comma-separated is standard for vocabulary biasing. Full prompt control deferred as separate feature. |
| Filler removal mechanism | Regex post-processing in `TextPostprocessor.normalize()` | Whisper token suppression; LLM-based semantic filtering | whisper-server HTTP API doesn't expose per-request token suppression. LLM requires AI cleanup dependency. Regex is practical and configurable. |
| Filler pattern matching | Auto-generate word-boundary-anchored regex with repeated-character support from simple word entries | Exact string matching only; user writes raw regex | Auto-generation handles elongated variants (euuuuh) transparently. Word boundaries prevent matching inside real words. |
| Built-in filler defaults | Unambiguous interjections only (EN: um, uh, hmm, mm, mhm, mmm, ah, oh, er; FR: euh, bah, beh, ben, hein, mmh, mh, pfff) | Include borderline fillers (like, you know, genre, tu vois) | Borderline fillers are also real words/phrases. Users can add them to custom patterns if desired. |
| Filler removal default state | Enabled by default | Disabled by default | Users are already manually removing fillers; enabling by default matches the expected behavior. Can be disabled in config. |
| Punctuation cleanup after filler removal | Clean orphaned commas and collapse double spaces | No punctuation cleanup | "I went to the, euh, store" should become "I went to the store", not "I went to the, , store". |
| Vocabulary language merging | Global + active language merged; only global for "auto" | Global only; per-language only | Supports both universal terms (RSSI) and language-specific terms. "auto" mode can't know which language list to use. |

## 4) External Dependencies & Costs

### Required external changes

None. This is a code-only change. Both features use existing whisper-server capabilities (the `prompt` field) and local regex processing.

### Cost impact

None. No cloud resources, API calls, or infrastructure changes.

## 5) Implementation Phases

### Phase 1: Vocabulary config and prompt delivery

**Goal**: Add `[vocabulary]` config section and send vocabulary as `prompt` form field to whisper-server.

#### 1a. Config dataclasses and loading (`config.py`)

Add `VocabularyConfig` dataclass and per-language vocabulary support:

```python
@dataclass
class VocabularyConfig:
    words: list[str] = field(default_factory=list)
    languages: dict[str, list[str]] = field(default_factory=dict)
```

Add `vocabulary` field to `AppConfig` (after line 169):

```python
@dataclass
class AppConfig:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    postprocess: PostprocessConfig = field(default_factory=PostprocessConfig)
    inject: InjectConfig = field(default_factory=InjectConfig)
    vocabulary: VocabularyConfig = field(default_factory=VocabularyConfig)
```

Update `load_config()` to parse the `[vocabulary]` section. The TOML structure uses `[vocabulary]` for global words and `[vocabulary.xx]` for per-language words. The tricky part: TOML sub-tables like `[vocabulary.en]` appear as nested dicts. We need to separate the `words` key from language-code keys:

```python
# In load_config(), after building `d` and before the AppConfig constructor:

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
    vocabulary=VocabularyConfig(words=vocab_words, languages=vocab_langs),
    filler=filler_cfg,
)
```

<!-- resolves review finding #2 (complete AppConfig constructor) and #3 (safe FillerConfig loading) -->

Add validation in `_validate()`:

```python
# Validate vocabulary language codes
for lang in config.vocabulary.languages:
    if lang not in WHISPER_LANGUAGES or lang == "auto":
        raise ValueError(
            f"Invalid vocabulary language {lang!r}, "
            "must be a whisper.cpp language code (not 'auto')"
        )
```

#### 1b. Prompt building and delivery (`transcribe.py`)

Add a `vocabulary` property to `WhisperClient` and include it in the POST:

```python
class WhisperClient:
    def __init__(self, server_url: str, language: str = "auto",
                 shutdown_event: threading.Event | None = None) -> None:
        self._language = language
        self._prompt: str = ""
        # ... rest unchanged ...

    @property
    def prompt(self) -> str:
        return self._prompt

    @prompt.setter
    def prompt(self, value: str) -> None:
        self._prompt = value
```

Update `_post_with_retry()` to include `prompt` in form data (line 48-55):

```python
data: dict[str, str] = {
    "temperature": "0.0",
    "response_format": "json",
    "language": self._language,
}
if self._prompt:
    data["prompt"] = self._prompt

resp = self._client.post(
    "/inference",
    files={"file": ("audio.wav", wav_bytes, "audio/wav")},
    data=data,
)
```

#### 1c. Vocabulary-to-prompt wiring (`app.py`)

Add a helper method to `SamWhispers` that builds the prompt from vocabulary config + current language, and call it at init and on language cycle:

```python
def _build_vocab_prompt(self) -> str:
    """Build initial_prompt string from vocabulary config and current language."""
    words = list(self.config.vocabulary.words)
    lang = self.whisper.language
    if lang != "auto" and lang in self.config.vocabulary.languages:
        words.extend(self.config.vocabulary.languages[lang])
    if not words:
        return ""
    # Deduplicate while preserving order <!-- resolves review finding #13 -->
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
            "Consider trimming the list.", len(unique)
        )  # <!-- resolves review finding #7 -->
    return ", ".join(unique)
```

In `__init__()`, after creating `self.whisper` (line 49):

```python
self.whisper.prompt = self._build_vocab_prompt()
```

In `_cycle_language()`, after setting `self.whisper.language` (line 134):

```python
self.whisper.prompt = self._build_vocab_prompt()
```

Log the vocabulary and filler config at startup in `_startup_checks()`:

```python
# Vocabulary logging
if self.config.vocabulary.words or self.config.vocabulary.languages:
    log.info("Vocabulary: %d global + %d language-specific words",
             len(self.config.vocabulary.words),
             sum(len(v) for v in self.config.vocabulary.languages.values()))

# Filler logging  <!-- resolves review finding #12 -->
if self.config.filler.enabled:
    filler_count = len(self.config.filler.words)
    builtin_label = "built-in + " if self.config.filler.use_builtins else ""
    log.info("Filler removal: enabled (%s%d custom words)", builtin_label, filler_count)
else:
    log.info("Filler removal: disabled")
```

#### 1d. Tests for Phase 1

**`tests/test_config.py`** -- add tests:
- `test_vocabulary_global_words`: load config with `[vocabulary] words = ["RSSI", "pynput"]`, verify `config.vocabulary.words`
- `test_vocabulary_per_language`: load config with `[vocabulary.fr] words = ["BLE"]`, verify `config.vocabulary.languages["fr"]`
- `test_vocabulary_invalid_language`: `[vocabulary.zzzz]` raises ValueError
- `test_vocabulary_auto_language_rejected`: `[vocabulary.auto]` raises ValueError
- `test_vocabulary_empty_default`: no `[vocabulary]` section → empty words and languages
- `test_vocabulary_merged_with_defaults`: partial vocabulary config merges correctly

**`tests/test_transcribe.py`** -- add tests:
- `test_prompt_sent_when_set`: set `client.prompt = "RSSI, pynput"`, verify `prompt` appears in POST form data
- `test_prompt_not_sent_when_empty`: default empty prompt → no `prompt` field in POST
- `test_prompt_property`: verify getter/setter

**`tests/test_app.py`** -- add tests (note: `_make_app()` replaces `app.whisper` with a mock post-init; tests must set `app.whisper.language = "fr"` etc. before calling `_build_vocab_prompt()`):
- `test_build_vocab_prompt_global_only`: global words, language="auto" → comma-joined global words
- `test_build_vocab_prompt_with_language`: global + fr words, language="fr" → merged
- `test_build_vocab_prompt_auto_language`: global + fr words, language="auto" → global only
- `test_build_vocab_prompt_empty`: no vocabulary → empty string
- `test_build_vocab_prompt_deduplicates`: global=["RSSI"], en=["RSSI"] → "RSSI" (not "RSSI, RSSI") <!-- resolves review finding #13 -->
- `test_vocab_prompt_updates_on_language_cycle`: verify prompt rebuilds when language changes

**Exit criteria**:
- [x] `[vocabulary]` config section loads and validates correctly
- [x] `prompt` form field is sent to `/inference` when vocabulary is configured
- [x] Prompt merges global + per-language words based on active language
- [x] Prompt updates when language is cycled
- [x] All new tests pass
- [x] `make check` passes (lint + typecheck + tests)

**Implementation (2025-04-23, code: 2faa2c1, fix: befc3f1)**

Implemented Phase 1 (vocabulary config and prompt delivery) across four source files and three test files. Added `VocabularyConfig` and `FillerConfig` dataclasses to `config.py` along with the `BUILTIN_FILLERS` constant, updated `AppConfig` with `vocabulary` and `filler` fields, added manual TOML parsing for the `[vocabulary]` section (separating `words` from language sub-table keys) and explicit field extraction for `[filler]`, and added vocabulary language code validation in `_validate()`. In `transcribe.py`, added a `_prompt` attribute with property/setter and updated `_post_with_retry()` to conditionally include `prompt` in the POST form data. In `app.py`, added `_build_vocab_prompt()` with case-insensitive deduplication and token limit warning, wired it in `__init__()` and `_cycle_language()`, and added vocabulary/filler logging in `_startup_checks()`. Added 6 config tests, 3 transcribe tests, and 6 app tests covering global words, per-language merging, auto-language behavior, deduplication, prompt delivery, and language cycle prompt rebuilding.

### Phase 2: Filler word removal

**Goal**: Add configurable regex-based filler word removal to `TextPostprocessor` with built-in defaults and elongated variant support.

#### 2a. Built-in filler defaults and config (`config.py`)

Define built-in filler word lists as a module-level constant:

```python
BUILTIN_FILLERS: dict[str, list[str]] = {
    "en": ["um", "uh", "hmm", "mm", "mhm", "mmm", "ah", "oh", "er"],
    "fr": ["euh", "bah", "beh", "ben", "hein", "mmh", "mh", "pfff"],
}
```

Add `FillerConfig` dataclass:

```python
@dataclass
class FillerConfig:
    enabled: bool = True
    words: list[str] = field(default_factory=list)  # user-defined additional fillers
    use_builtins: bool = True  # whether to include BUILTIN_FILLERS  <!-- resolves review finding #11 -->
```

Add `filler` field to `AppConfig`:

```python
@dataclass
class AppConfig:
    # ... existing fields ...
    filler: FillerConfig = field(default_factory=FillerConfig)
```

Update `load_config()` to parse `[filler]` section (uses explicit field extraction, not `**` unpacking, to reject unexpected TOML keys safely):

```python
# Already shown in Phase 1a's complete AppConfig constructor above.
# The filler_raw extraction and FillerConfig construction is there.
```

#### 2b. Filler regex engine (`postprocess.py`)

Add a `FillerRemover` class that compiles regex patterns from filler word lists:

```python
class FillerRemover:
    """Remove filler words using word-boundary-anchored regex with elongation support."""

    def __init__(self, words: list[str]) -> None:
        self._pattern: re.Pattern[str] | None = None
        if words:
            alternatives = [self._build_pattern(w) for w in words]
            combined = "|".join(alternatives)
            # Match filler word, optionally preceded by comma+space or space
            # and followed by comma+space, space, or end-of-string.
            # Use case-insensitive matching.
            self._pattern = re.compile(
                r"(?<!\w)(?:" + combined + r")(?!\w)",
                re.IGNORECASE,
            )

    @staticmethod
    def _build_pattern(word: str) -> str:
        """Build regex pattern allowing repeated characters.

        "euh" → "e+u+h+"
        "pfff" → "p+f+"  (collapse consecutive identical chars)
        "mmh" → "m+h+"
        """
        parts: list[str] = []
        prev = ""
        for ch in word.lower():
            if ch == prev:
                continue  # skip consecutive duplicates, the + handles them
            if ch.isalpha():
                parts.append(re.escape(ch) + "+")
            else:
                parts.append(re.escape(ch))
            prev = ch
        return "".join(parts)

    def remove(self, text: str) -> str:
        """Remove filler words and clean up orphaned punctuation."""
        if not self._pattern:
            return text

        # Remove filler words
        text = self._pattern.sub("", text)

        # Clean orphaned punctuation: ", ," → ","  and "the,  store" → "the store"
        text = re.sub(r",\s*,", ",", text)          # double commas
        text = re.sub(r",\s+([.!?])", r"\1", text)  # comma before sentence-end punct
        text = re.sub(r"\s,\s", " ", text)           # orphaned comma with spaces
        text = re.sub(r"(?<=\w),\s{2,}", ", ", text) # comma followed by excess space
        # Note: double-space collapse is handled by normalize()'s collapse_spaces step

        return text
```

#### 2c. Wire filler removal into TextPostprocessor (`postprocess.py`)

<!-- resolves review findings #6 (unused language param removed), #14 (decoupled from FillerConfig) -->

Update `TextPostprocessor.__init__()` to accept a plain list of filler words. The config-to-word-list resolution happens in `app.py`, keeping `postprocess.py` config-agnostic:

```python
from samwhispers.config import PostprocessConfig, _TRAILING_MAP

class TextPostprocessor:
    def __init__(self, config: PostprocessConfig,
                 filler_words: list[str] | None = None) -> None:
        self._config = config
        self._filler_remover: FillerRemover | None = None
        if filler_words:
            self._filler_remover = FillerRemover(filler_words)
```

<!-- resolves review finding #4: reorder to avoid redundant space collapse -->

Reorder `normalize()` so filler removal runs before `collapse_spaces` (filler removal's internal space collapse becomes unnecessary -- remove it from `FillerRemover.remove()`):

```python
def normalize(self, text: str) -> str:
    """Collapse newlines, remove fillers, collapse whitespace, and trim. Run before cleanup."""
    if self._config.collapse_newlines:
        text = text.replace("\n", " ")

    if self._filler_remover:
        text = self._filler_remover.remove(text)

    if self._config.collapse_spaces:
        text = re.sub(r" {2,}", " ", text)

    if self._config.trim:
        text = text.strip()

    return text
```

#### 2d. Wire filler config into app (`app.py`)

Build the filler word list from config and pass it to `TextPostprocessor`. Update `SamWhispers.__init__()` (line 54):

```python
# Build filler word list from config
filler_words: list[str] | None = None
if config.filler.enabled:
    words: list[str] = list(config.filler.words)
    if config.filler.use_builtins:
        from samwhispers.config import BUILTIN_FILLERS
        for lang_words in BUILTIN_FILLERS.values():
            words.extend(lang_words)
    if words:
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for w in words:
            wl = w.lower()
            if wl not in seen:
                seen.add(wl)
                unique.append(w)
        filler_words = unique

self.postprocessor = TextPostprocessor(
    config.postprocess,
    filler_words=filler_words,
)
```

#### 2e. Tests for Phase 2

**`tests/test_config.py`** -- add tests:
- `test_filler_defaults`: no `[filler]` section → enabled=True, use_builtins=True, words=[]
- `test_filler_disabled`: `[filler] enabled = false` → disabled
- `test_filler_custom_words`: `[filler] words = ["hum", "bof"]` → loaded
- `test_filler_builtins_disabled`: `[filler] use_builtins = false` → only user words used

**`tests/test_postprocess.py`** -- add tests (use a `_make_with_filler(words)` helper that creates `TextPostprocessor` with a filler word list):
- `test_filler_removal_basic`: "I went to the euh store" → "I went to the store"
- `test_filler_removal_elongated`: "I went to the euuuuuh store" → "I went to the store"
- `test_filler_removal_with_comma`: "I went to the, euh, store" → "I went to the store"
- `test_filler_removal_start_of_text`: "Euh I went to the store" → "I went to the store"
- `test_filler_removal_end_of_text`: "I went to the store euh" → "I went to the store"
- `test_filler_removal_multiple`: "euh I went euh to the euh store" → "I went to the store"
- `test_filler_removal_case_insensitive`: "EUH I went to the store" → "I went to the store"
- `test_filler_removal_repeated_chars`: "mmmmmh okay" → "okay"
- `test_filler_removal_no_partial_match`: "behead" stays "behead" (not matched by "beh")
- `test_filler_removal_disabled`: no filler_words → text unchanged
- `test_filler_removal_custom_words`: custom word "hum" is removed
- `test_filler_removal_empty_result`: text that is only fillers → empty string after trim
- `test_filler_build_pattern`: unit test `_build_pattern()` for various inputs
- `test_filler_removal_preserves_real_words`: "oh" removed but "ohm" preserved; "ben" removed but "benefit" preserved; "err" preserved (standalone word "to err is human") <!-- resolves review finding #8 -->
- `test_filler_all_fillers_pipeline`: full normalize+finalize on all-filler text produces empty string (finalize returns "" for empty input) <!-- resolves review finding #9 -->

**Exit criteria**:
- [x] Filler removal correctly strips built-in English and French fillers
- [x] Elongated variants are caught (euuuuh, mmmmmh, etc.)
- [x] Word boundaries prevent partial matches inside real words
- [x] Orphaned punctuation is cleaned up after filler removal
- [x] Feature can be disabled via config
- [x] Custom filler words work alongside or instead of builtins
- [x] All new tests pass
- [x] `make check` passes

**Implementation (2025-04-23, code: c382502)**

Implemented Phase 2 (filler word removal) across 4 source files and 2 test files. Added `FillerRemover` class to `postprocess.py` with `_build_pattern()` (auto-generates regex allowing repeated characters, word-boundary anchored) and `remove()` (removes fillers + cleans orphaned punctuation with simplified 2-rule cleanup). Updated `TextPostprocessor.__init__()` to accept `filler_words: list[str] | None = None`, keeping postprocess.py config-agnostic. Reordered `normalize()`: collapse_newlines -> filler_removal -> collapse_spaces -> trim. In `app.py`, builds filler word list from `config.filler` with builtin merging and deduplication, passes to `TextPostprocessor`. Added 20 postprocess tests and 4 config tests covering all planned scenarios plus comma-before-period cleanup.

### Phase 3: Config examples and documentation

**Goal**: Update config files and documentation to reflect both new features.

#### 3a. Update `config.example.toml`

Add vocabulary and filler sections with comments:

```toml
[postprocess]
collapse_newlines = true     # Replace \n from whisper segments with spaces
collapse_spaces = true       # Collapse multiple spaces into one
trim = true                  # Strip leading/trailing whitespace
trailing = "newline"         # Append after text: "none", "space", "newline", "double_newline", "tab"

[vocabulary]
# Words to bias Whisper toward recognizing (sent as initial_prompt).
# Keep this list short and broadly applicable -- domain-specific jargon
# for a single conversation may cause mild misrecognition in unrelated contexts.
# Best for: proper nouns, project names, technical terms you use frequently.
# Token limit: ~150-200 words total (shared with per-language lists).
words = []

# Per-language vocabulary (merged with global words when that language is active).
# [vocabulary.en]
# words = ["Bluetooth Low Energy"]
# [vocabulary.fr]
# words = ["Bluetooth basse consommation"]

[filler]
enabled = true          # Remove filler words from transcription
use_builtins = true     # Include built-in filler lists (English + French)
words = []              # Additional filler words to remove (elongated variants auto-detected)
# Built-in English: um, uh, hmm, mm, mhm, mmm, ah, oh, er
# Built-in French: euh, bah, beh, ben, hein, mmh, mh, pfff
```

<!-- resolves review finding #10: adds missing [postprocess] to config.example.toml -->

#### 3b. Update `config.toml` (user's active config)

Add the same sections with the user's likely defaults.

#### 3c. Update `README.md`

Add sections documenting:
- Custom vocabulary configuration and usage
- Filler word removal configuration
- How elongated variants work
- How to add custom filler words
- How per-language vocabulary merging works

#### 3d. Update `docs/ROADMAP.md`

Mark "custom vocab" and "filler words removal" as implemented.

**Exit criteria**:
- [x] `config.example.toml` includes documented vocabulary and filler sections
- [x] `config.toml` includes the new sections
- [x] `README.md` documents both features
- [x] `docs/ROADMAP.md` updated
- [x] `make check` passes

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Vocabulary pollution: irrelevant words in `initial_prompt` cause misrecognition | Low-Medium: mild decoder bias, unlikely to cause major errors with short lists | Config comment warning to keep list broadly applicable. Vocabulary profiles deferred as future enhancement. |
| Filler false positives: regex removes intentional text (e.g., quoting someone saying "euh") | Low: rare in dictation use case | Ship only unambiguous interjections as defaults. Feature is configurable and can be disabled. User types fillers manually in rare intentional cases. |
| Filler regex matches inside real words | Medium: could corrupt output silently | Word-boundary anchoring (`\b` equivalent via `(?<!\w)` / `(?!\w)`). Thorough test coverage with words like "behead", "ohm", "benefit". |
| `initial_prompt` token limit exceeded | Low: natural cap at ~150-200 words | Log a warning if the merged vocabulary exceeds a reasonable threshold (e.g., 100 words). |
| Orphaned punctuation after filler removal | Low-Medium: cosmetic but noticeable | Dedicated punctuation cleanup regex pass after filler removal. Test with comma-surrounded fillers. |
| `TextPostprocessor` constructor signature change breaks existing callers | Low: only constructed in `app.py` and tests | New parameter is optional with default `None`. Existing test helper `_make()` continues to work. |

## 7) Verification

**Automated**:
```bash
make check   # lint + typecheck + tests
```

**Manual**:
1. Add vocabulary words to `config.toml`, run SamWhispers, dictate text containing those words, verify improved recognition
2. Dictate text with French fillers ("euh", "bah"), verify they are removed from output
3. Dictate text with elongated fillers ("euuuuuh"), verify removal
4. Set `filler.enabled = false`, verify fillers pass through
5. Set `filler.builtins = false`, add custom word, verify only custom word is removed
6. Cycle language, verify vocabulary prompt updates (check verbose log output)

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `config.example.toml` | Add `[vocabulary]` and `[filler]` sections with comments | Phase 3 |
| `config.toml` | Add `[vocabulary]` and `[filler]` sections | Phase 3 |
| `README.md` | Document vocabulary and filler features in Config Options and new sections | Phase 3 |
| `docs/ROADMAP.md` | Mark custom vocab and filler removal as done | Phase 3 |

## 9) Implementation Divergences from Plan

1. **Phase 2 -- Simplified orphaned punctuation cleanup**: Plan specified 4 regex rules in `FillerRemover.remove()`. Implementation uses 2 rules: (a) remove double commas entirely (`r",\s*,"` → `""`) instead of collapsing to single comma, and (b) comma before sentence-end punct. Rules (c) orphaned comma with spaces and (d) comma followed by excess space were dropped because `collapse_spaces` in `normalize()` handles leftover whitespace after filler removal. The simplified approach produces correct output with fewer regex passes.

## Review Log

### 2025-04-23 -- Plan Creation Review (via /plan)

14 findings (2 High, 6 Medium, 6 Low). 14 auto-resolved.

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | Line numbers in config.py off by 30-105 lines, app.py by ~42 lines | Medium | Resolved -- updated all line references to match current source |
| 2 | `load_config()` vocabulary parsing needs complete `AppConfig(...)` constructor | High | Resolved -- added complete constructor with both new fields |
| 3 | `FillerConfig(**d.get("filler", {}))` may break on unexpected TOML keys | High | Resolved -- switched to explicit field extraction like CleanupConfig |
| 4 | Redundant double-space collapse in `normalize()` and `FillerRemover.remove()` | Medium | Resolved -- reordered: filler removal before collapse_spaces; removed redundant collapse from FillerRemover |
| 5 | `_build_vocab_prompt` tests need mock setup for `whisper.language` | Medium | Resolved -- added note in test descriptions |
| 6 | Unused `language` parameter on `TextPostprocessor` constructor | Low | Resolved -- removed; postprocessor accepts plain word list instead of FillerConfig |
| 7 | Missing token limit warning implementation | Low | Resolved -- added warning in `_build_vocab_prompt()` when >100 words |
| 8 | `"er"` pattern matches `"err"` (real word) | Medium | Resolved -- added `"err"` to false-positive test cases |
| 9 | All-fillers case may produce empty text through cleanup+inject | Low | Resolved -- added `test_filler_all_fillers_pipeline` test case |
| 10 | Missing `[postprocess]` in `config.example.toml` | Low | Resolved -- added to Phase 3a config snippet |
| 11 | `FillerConfig.builtins` naming ambiguity | Low | Resolved -- renamed to `use_builtins` |
| 12 | Missing filler config logging at startup | Low | Resolved -- added filler logging alongside vocabulary logging |
| 13 | Missing vocab deduplication in `_build_vocab_prompt` | Low | Resolved -- added deduplication with order preservation |
| 14 | Coupling: `TextPostprocessor` imports `FillerConfig` + `BUILTIN_FILLERS` | Medium | Resolved -- postprocessor now accepts `list[str]`; config resolution moved to `app.py` |

Reviewed by: Implementability reviewer (confidence: 78%), Maintainability reviewer (confidence: 82%). All findings auto-resolved.

### 2025-04-23 -- Implementation Review (after Phase 1, persona: Implementability reviewer)

Implementation health: Green (after auto-fix cycle).
7 findings (1 High, 2 Medium, 4 Low). 2 auto-resolved, 5 noted.

| # | Persona | Finding | Severity | Confidence | Resolution |
|---|---|---|---|---|---|
| 1 | Implementability | `ruff format` fails on 5 Phase 1 modified files | High | 100% | Resolved -- ran `ruff format` in fix commit befc3f1 |
| 2 | Implementability | Pre-existing `ruff check` F401 in inject.py | Medium | 100% | Noted -- pre-existing, out of scope for Phase 1 |
| 3 | Implementability | `test_vocab_prompt_updates_on_language_cycle` doesn't verify wiring to `whisper.prompt` | Medium | 90% | Resolved -- fixed in befc3f1 to assert `app.whisper.prompt` after `_cycle_language()` |
| 4 | Implementability | `FillerConfig` + `BUILTIN_FILLERS` added but not consumed until Phase 2 | Low | 95% | Noted -- deliberate front-loading per plan |
| 5 | Implementability | `data` variable shadowed in `_post_with_retry` | Low | 100% | Noted -- pre-existing, out of scope |
| 6 | Implementability | No test for >100 words vocabulary warning | Low | 95% | Noted -- simple code path, low regression risk |
| 7 | Implementability | `config.toml` missing vocabulary/filler sections | Low | 100% | Noted -- Phase 3 scope |
