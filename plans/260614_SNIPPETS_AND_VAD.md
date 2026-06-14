# Snippets & Voice Activity Detection

> **Date**: 2026-06-14
> **Status**: In Progress
> **Scope**: Add voice text-replacement snippets and VAD (server-side + client-side auto-stop)
> **Estimated effort**: 2-3 days

---

## Intent

### Problem statement & desired outcomes

SamWhispers lacks two commonly-wanted dictation features:

1. **Snippets** — trigger phrases that expand to saved text (a voice text-expander). Users who repeatedly dictate the same boilerplate (addresses, signatures, code blocks) must say the full text every time.
2. **Voice Activity Detection** — using whisper.cpp's built-in VAD to improve transcription quality (trim silence) and auto-stopping recording in toggle mode after silence (UX improvement removing the need to manually press the stop key).

Desired outcomes:
- Users can define trigger→expansion mappings and have them substituted in transcriptions automatically.
- Server-side VAD trims silence from audio before decoding, improving transcription quality.
- Client-side VAD auto-stops recording in toggle mode after configurable silence duration, providing a natural end-of-dictation experience.

### Success criteria

1. Snippets: defining a trigger→expansion pair in config and speaking the trigger results in the expansion being injected in batch mode and streaming preview mode.
2. Snippets: a configurable toggle (default on) adds snippet triggers to the Whisper vocabulary prompt to improve recognition.
3. Snippets: matching is exact-phrase, case-insensitive, word-boundary-anchored.
4. VAD server-side: enabling VAD in config passes the appropriate flags (`--vad`, `--vad-model`, threshold, etc.) to the managed whisper-server.
5. VAD client-side: in toggle mode only, recording auto-stops after the configured silence duration (default 10s). Hold mode is unaffected.
6. VAD client-side auto-stop also works during streaming toggle mode sessions.
7. VAD model downloadable via `samwhispers-setup` and on-demand from the web UI.
8. Web UI: snippets configurable on a page near vocabulary; VAD on its own dedicated page.
9. Both features disabled by default (additive, non-breaking).

### Scope boundaries & non-goals

**In scope:**
- Snippet trigger→expansion config (`[snippets]` TOML dict), matching engine, pipeline integration
- Snippet vocabulary biasing toggle (`snippets.bias_recognition`)
- Server-side VAD config fields and `_build_cmd()` integration
- Client-side silence detection in AudioRecorder for toggle mode only
- VAD model download in setup + web UI
- Unified `[vad]` config section
- Web UI pages for both features
- Documentation updates

**Non-goals:**
- Personal dictionary / auto-learn (dropped from scope — needs separate exploration)
- Regex-based or prefix-based snippet matching (only exact phrase match)
- Snippets in progressive streaming mode (documented limitation — words are injected live before full phrase is available)
- Client-side VAD in hold mode (user controls endpoint by releasing key)
- Streaming commit-point integration with VAD (roadmap item "streaming window trimming" — separate future work)

---

## Discovery

### Existing patterns & constraints

- FillerRemover (postprocess.py:14-65) is the direct precedent: word-boundary-anchored regex, case-insensitive, with elongation support. Snippet matcher follows the same pattern but with replacement text.
- Config system: all `@dataclass`es in config.py, validated in `_validate()`, serialized in `webconfig.py:to_toml_dict()`, round-tripped via `PUT /api/config`. New sections must be handled in `build_config()` and `to_toml_dict()`.
- `_build_cmd()` (server.py:68-73) currently passes only `-m`, `--host`, `--port`, `-sns`. VAD flags are conditional additions.
- `compute_level()` (audio.py:28-37) already computes RMS per audio frame — client-side VAD can use this directly.
- AudioRecorder (audio.py) has timer-based auto-stop via `_auto_stop_timer` and `on_auto_stop` callback — client-side VAD follows the same pattern.
- whisper.cpp VAD flags: `--vad`, `--vad-model`, `--vad-threshold`, `--vad-min-speech-duration-ms`, `--vad-min-silence-duration-ms`, `--vad-max-speech-duration-s`, `--vad-speech-pad-ms`, `--vad-samples-overlap`.
- Token budget: ~224 tokens for initial_prompt. Existing >100 words warning in `_build_prompt()` (app.py:186-190) covers overflow from snippet biasing.
- `requires_restart()` returns True for any config change. `whisper_restarted` gated on `old.whisper != new.whisper` — VAD fields in a `[vad]` section need custom restart logic to trigger whisper-server restart for server-side VAD changes.
- Progressive streaming injects words immediately (app.py:276-284) — no full-text pass available for snippet matching.

### Risks & mitigations

| Risk | Mitigation |
|---|---|
| Snippet trigger contains a filler word → eaten by filler removal | Snippets run after filler removal; document limitation |
| Token budget overflow with many snippet triggers + vocabulary | Existing >100 word warning applies; bias toggle lets user disable |
| VAD model not downloaded → server fails to start with `--vad` | Validate model path in `_validate()` when VAD enabled; clear error message |
| 10s silence auto-stop too aggressive for some users | Configurable; documented default; toggle-mode only |
| VAD config changes need whisper-server restart but live in `[vad]` not `[whisper]` | Custom logic in `apply_config_change` to detect server-relevant VAD field changes |

### Resolved decisions

- Q1: All features in one plan — A: "1 plan" — Decision: Single combined plan for snippets + VAD
- Q2: Snippet matching strategy — A: "a and c" then revised to "only exact phrase match" — Decision: Exact phrase match only, case-insensitive, word-boundary-anchored
- Q4: Snippet pipeline position — A: ok — Decision: Snippets run after filler removal (inside normalize or between normalize and cleanup)
- Q5 (revised): Auto-learn scope — A: "b" (drop) — Decision: Personal dictionary dropped from scope; not meaningfully different from existing vocabulary without auto-learn
- Q6: VAD scope — A: ok — Decision: Both server-side (flags) and client-side (auto-stop) VAD
- Q7: VAD model management — A: ok — Decision: Both setup download and web UI on-demand download
- Q8: Client-side VAD defaults — A: ok, then revised to 10s — Decision: threshold=0.01, duration=10.0s (configurable)
- Q9: Snippet config format — A: ok — Decision: TOML dict under `[snippets]` (key=trigger, value=expansion)
- Q10: Snippet vocabulary biasing — A: "c enabled by default" — Decision: Configurable toggle `snippets.bias_recognition`, default true, UI checkbox
- Q11: Progressive streaming limitation — A: "a" — Decision: Document as known limitation; snippets don't work in progressive mode
- Q12: Client-side VAD in streaming — A: "yes, 10s" — Decision: Auto-stop applies to streaming toggle mode too; duration default 10s
- Q13: VAD config location — A: "b" — Decision: Unified `[vad]` section for both server-side and client-side settings
- Q14: Web UI placement — A: snippets near dictionary, VAD own page — Decision: Snippets page adjacent to vocabulary in nav; VAD gets dedicated page

### Open items

None.

### Recommended approach

1. **Snippets config + matching engine**: New `SnippetConfig` dataclass with `items: dict[str, str]` and `bias_recognition: bool = True`. A `SnippetExpander` class (similar to `FillerRemover`) that builds a combined regex from all trigger phrases and replaces matches with expansion text.
2. **Snippets pipeline integration**: Call `SnippetExpander.expand(text)` after `postprocessor.normalize()` and before `cleanup.cleanup()` in both batch and streaming-preview paths.
3. **Snippets vocabulary biasing**: In `_build_prompt()`, if `bias_recognition` is true, add snippet trigger phrases to the vocabulary word list.
4. **VAD server-side config**: New `VadConfig` dataclass with `enabled`, `model_path`, `threshold`, `min_speech_duration_ms`, `min_silence_duration_ms`, etc. Conditionally append flags in `_build_cmd()`.
5. **VAD client-side auto-stop**: In `AudioRecorder._callback()`, track consecutive frames below threshold. When silence exceeds duration AND mode is toggle, fire `on_auto_stop`. Skip entirely in hold mode.
6. **VAD model management**: Extend `samwhispers-setup` to download default Silero VAD model. Add download endpoint in web UI (similar to whisper model downloads).
7. **VAD restart logic**: Custom check in config-change flow to detect server-side VAD field changes and trigger whisper-server restart.
8. **Web UI**: Snippets page (key-value editor with add/remove) near vocabulary nav item. VAD page with enable toggle, model selector, threshold sliders, client-side duration/threshold.
9. **Tests**: New test files for snippet expander and client-side VAD logic.


## 1) Current State

### Snippets-relevant code

- **postprocess.py:14-65** — `FillerRemover`: word-boundary-anchored regex substitution engine. Builds patterns from word lists, handles elongation, case-insensitive. Direct precedent for snippet matching.
- **app.py:480-483** (batch pipeline) — `text = self.postprocessor.normalize(text)` followed by `text = self.cleanup.cleanup(text)`. The snippet expansion point is between these two calls.
- **app.py:389** (streaming preview) — `text = self.postprocessor.normalize(raw_text)` then `text = self.cleanup.cleanup(text)`. Same insertion point.
- **config.py:231-234** — `VocabularyConfig` with `words: list[str]` and `languages: dict[str, list[str]]`. Pattern for new config sections.
- **app.py:173-199** — `_build_prompt()` merges vocabulary words + accent into initial_prompt.
- **webconfig.py:108-130** — `to_toml_dict()` serializes AppConfig back to TOML-shaped dict.
- **webconfig.py:132-150** — `save_config_dict()` validates + atomic writes.

### VAD-relevant code

- **server.py:68-73** — `_build_cmd()` returns `[bin, -m, model, --host, host, --port, port, -sns]`. VAD flags append here.
- **audio.py:30-38** — `compute_level()` returns RMS-based 0.0-1.0 level per frame. VAD can reuse this.
- **audio.py:77-82** — `AudioRecorder.__init__` has `_timer` for max-duration auto-stop. Client-side VAD parallels this mechanism.
- **audio.py:82-85** — `_callback()` fires per audio frame with `on_level` callback. Silence tracking would live here.
- **audio.py:119-122** — `_auto_stop()` method called by timer. Client-side VAD would call the same `_on_auto_stop` callback.
- **config.py:213-220** — `WhisperConfig` dataclass. Server-side VAD flags could go here OR in a separate `VadConfig`.
- **webserver.py:178-188** — `PUT /api/config` compares `old_cfg.whisper != new_cfg.whisper` to gate whisper-server restart.
- **bootstrap.py** — `samwhispers-setup` handles model downloads. VAD model download extends this.

### Config round-trip pattern

All config sections follow: `@dataclass` in config.py → field in `AppConfig` → parsed in `build_config()` → serialized in `to_toml_dict()` → validated in `_validate()` → UI reads via `GET /api/config` → UI writes via `PUT /api/config` → `requires_restart()` triggers worker restart.

## 2) Goal

Add two features: (1) a snippet expander that substitutes trigger phrases with saved expansions in the transcription pipeline, and (2) voice activity detection with both server-side (whisper.cpp flags) and client-side (auto-stop on silence in toggle mode) support.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Snippet matching | Exact phrase, case-insensitive, word-boundary-anchored | Regex-based; prefix/keyword match | Predictable, same pattern as FillerRemover, no regex knowledge needed |
| Snippet pipeline position | After filler removal, before AI cleanup | Before filler removal; after cleanup | Triggers intact after filler removal; AI cleanup may "fix" expansions if it runs first |
| Snippet config format | TOML nested `[snippets.items]` sub-table | Flat dict (all non-reserved keys are items) | Clean separation avoids key collision on typos; explicit items namespace |
| Snippet vocabulary biasing | Configurable toggle `bias_recognition`, default true | Always bias; never bias | Transparency + user control; enabled by default for recognition accuracy |
| VAD scope | Both server-side and client-side | Server-only; client-only | Server-side is trivial (flags); client-side is the bigger UX win. Independent, complement each other |
| Client-side VAD scope | Toggle mode only | All modes; configurable per mode | Hold mode has explicit endpoint (key release); client-side VAD is redundant there |
| Client-side VAD defaults | threshold=0.01, duration=10.0s | 2s duration; 0.05 threshold | 10s avoids cutting off thinking pauses especially during streaming |
| VAD config location | Unified `[vad]` section | Split across `[whisper]` and `[audio]` | Single discoverable location for all VAD settings |
| VAD model management | Setup + web UI on-demand download | Setup only; UI only | Matches existing whisper model pattern |
| Progressive streaming | Documented limitation — snippets don't work | Buffer words for matching | Buffering defeats progressive mode's instant-injection value |

## 4) External Dependencies & Costs

### Required external changes

None. Both features are fully local. VAD model is a ~2MB ONNX file downloaded from whisper.cpp's releases.

### Cost impact

None. No cloud API usage, no recurring costs.

## 5) Implementation Phases

### Phase 1: Snippet engine — config, matching, and pipeline integration [QA]

**Goal**: End-to-end snippet expansion in batch and streaming-preview modes.

**File scope**: `config.py`, `postprocess.py`, `app.py`, `webconfig.py`, `config.example.toml`, `tests/test_postprocess.py`, `tests/test_config.py`, `tests/test_app.py`

**1.1 Config (config.py)**

Add `SnippetConfig` dataclass and wire into `AppConfig`:

```python
@dataclass
class SnippetConfig:
    items: dict[str, str] = field(default_factory=dict)  # trigger -> expansion
    bias_recognition: bool = True  # add triggers to vocabulary prompt
    enabled: bool = True  # master toggle
```

Add to `AppConfig`:
```python
snippets: SnippetConfig = field(default_factory=SnippetConfig)
```

Parse in `build_config()` — use a nested `[snippets.items]` sub-table to cleanly separate config fields from snippet data (prevents key collisions where a typo like `bais_recognition` silently becomes a snippet trigger):

```python
snippets_raw = d.get("snippets", {})
items_raw = snippets_raw.get("items", {})
snippets_cfg = SnippetConfig(
    items=dict(items_raw),
    bias_recognition=snippets_raw.get("bias_recognition", True),
    enabled=snippets_raw.get("enabled", True),
)
```

Validate: reject empty trigger strings or empty expansion strings with clear errors.

TOML format:
```toml
[snippets]
enabled = true
bias_recognition = true

[snippets.items]
"my address" = "123 Main St, City, 12345"
sig = "Best regards,\nJohn Doe"
```

Serialize in `to_toml_dict()`:
```python
data["snippets"] = {
    "enabled": config.snippets.enabled,
    "bias_recognition": config.snippets.bias_recognition,
    "items": dict(config.snippets.items),
}
```

**1.2 Matching engine (postprocess.py)**

Add `SnippetExpander` class below `FillerRemover`:

```python
class SnippetExpander:
    """Replace trigger phrases with saved expansions (exact match, word-boundary-anchored)."""

    def __init__(self, items: dict[str, str]) -> None:
        self._replacements: list[tuple[re.Pattern[str], str]] = []
        # Sort by trigger length descending to match longest first
        for trigger in sorted(items, key=len, reverse=True):
            pattern = re.compile(
                r"(?<!\w)" + re.escape(trigger) + r"(?!\w)",
                re.IGNORECASE,
            )
            self._replacements.append((pattern, items[trigger]))

    def expand(self, text: str) -> str:
        for pattern, expansion in self._replacements:
            text = pattern.sub(expansion, text)
        return text
```

**1.3 Pipeline integration (app.py)**

In `SamWhispers.__init__`, build expander:
```python
self._snippet_expander: SnippetExpander | None = None
if config.snippets.items:
    from samwhispers.postprocess import SnippetExpander
    self._snippet_expander = SnippetExpander(config.snippets.items)
```

In `_build_prompt()`, add snippet trigger biasing after vocabulary words:
```python
if self.config.snippets.bias_recognition and self.config.snippets.items:
    words.extend(self.config.snippets.items.keys())
```

In `_process_recording()`, after `text = self.postprocessor.normalize(text)` and before `text = self.cleanup.cleanup(text)`:
```python
if self._snippet_expander:
    text = self._snippet_expander.expand(text)
```

Same insertion in `_inject_final_paragraph()` (streaming preview path).

**1.4 Tests**

- `tests/test_postprocess.py`: Test `SnippetExpander` — basic expansion, case-insensitive, word-boundary (no partial match), longest-first ordering, multi-line expansion.
- `tests/test_config.py`: Test snippet config parsing from TOML dict, `bias_recognition` default, round-trip via `to_toml_dict`.
- `tests/test_app.py`: Test that snippet expansion runs in batch pipeline.

**Exit criteria**:
- [x] `SnippetConfig` dataclass with `items` and `bias_recognition` fields
- [x] `SnippetExpander` class with `expand()` method
- [x] Snippet expansion wired into batch pipeline (after normalize, before cleanup)
- [x] Snippet expansion wired into streaming preview pipeline
- [x] `bias_recognition` adds snippet triggers to vocabulary prompt
- [x] Config round-trip: `build_config()` + `to_toml_dict()` handles snippets
- [x] Tests pass for expander, config parsing, and pipeline integration
- [x] Update README.md Snippets section with config format and usage

**Implementation (2026-06-14, code: c2c180a, fix: 107a0bd)**
Added the snippet expansion feature to SamWhispers. A new `SnippetConfig` dataclass (with `items` dict, `bias_recognition` bool, `enabled` bool) is parsed from a nested `[snippets.items]` TOML sub-table, validated (rejecting empty triggers/expansions), and round-tripped through `to_toml_dict()`. The `SnippetExpander` class in `postprocess.py` performs exact-phrase, case-insensitive, word-boundary-anchored matching with longest-first ordering. It is wired into both the batch pipeline (`_process_recording`) and streaming preview pipeline (`_inject_final_paragraph`) — after `normalize()` and before `cleanup()`. When `bias_recognition` is enabled, snippet trigger keys are added to the Whisper vocabulary prompt via `_build_prompt()`. The `config.example.toml` includes the new section, and README.md documents the feature with setup, behavior, and tips. Post-review fix: `pattern.sub(expansion, text)` → `pattern.sub(lambda m: expansion, text)` to prevent `re.sub` template interpretation of backslashes/groups in expansion text.

### Phase 2: VAD — server-side flags, client-side auto-stop, model management [QA]

**Goal**: End-to-end VAD: server-side flags improve transcription; client-side auto-stops recording in toggle mode after silence.

**File scope**: `config.py`, `server.py`, `audio.py`, `app.py`, `supervisor.py`, `webconfig.py`, `webserver.py`, `bootstrap.py`, `config.example.toml`, `tests/test_config.py`, `tests/test_server.py`, `tests/test_audio.py`

**2.1 Config (config.py)**

Add `VadConfig` dataclass:

```python
@dataclass
class VadConfig:
    enabled: bool = False
    # Server-side (whisper.cpp --vad flags)
    model_path: str = ""  # path to VAD ONNX model
    threshold: float = 0.5  # speech probability threshold
    min_speech_duration_ms: int = 250
    min_silence_duration_ms: int = 100
    max_speech_duration_s: float = 0.0  # 0 = unlimited (FLT_MAX)
    speech_pad_ms: int = 30
    samples_overlap: float = 0.1
    # Client-side (auto-stop on silence, toggle mode only)
    silence_threshold: float = 0.01  # audio level below this = silence
    silence_duration: float = 10.0  # seconds of silence before auto-stop
```

Add to `AppConfig`:
```python
vad: VadConfig = field(default_factory=VadConfig)
```

Validation in `_validate()`:
- If `vad.enabled` and `vad.model_path` is set: check file exists
- `threshold` in 0.0-1.0, `silence_threshold` in 0.0-1.0, `silence_duration` > 0

**2.2 Server-side VAD (server.py)**

Modify `WhisperServerManager.__init__` to accept `VadConfig`:
```python
def __init__(self, config: WhisperConfig, vad_config: VadConfig | None = None) -> None:
    ...
    self._vad = vad_config
```

Extend `_build_cmd()`:
```python
def _build_cmd(self) -> list[str]:
    cmd = [self._bin, "-m", self._model, "--host", self._host, "--port", self._port, "-sns"]
    if self._vad and self._vad.enabled and self._vad.model_path:
        cmd.extend(["--vad", "-vm", str(Path(self._vad.model_path).resolve())])
        cmd.extend(["-vt", str(self._vad.threshold)])
        if self._vad.min_speech_duration_ms != 250:
            cmd.extend(["-vspd", str(self._vad.min_speech_duration_ms)])
        if self._vad.min_silence_duration_ms != 100:
            cmd.extend(["-vsd", str(self._vad.min_silence_duration_ms)])
        if self._vad.max_speech_duration_s > 0:
            cmd.extend(["-vmsd", str(self._vad.max_speech_duration_s)])
        if self._vad.speech_pad_ms != 30:
            cmd.extend(["-vp", str(self._vad.speech_pad_ms)])
        if self._vad.samples_overlap != 0.1:
            cmd.extend(["-vo", str(self._vad.samples_overlap)])
    return cmd
```

**2.3 Client-side VAD (audio.py)**

Add silence tracking to `AudioRecorder`:

```python
def __init__(self, ..., silence_threshold: float = 0.0, silence_duration: float = 0.0) -> None:
    ...
    self._silence_threshold = silence_threshold
    self._silence_duration = silence_duration
    self._silence_start: float | None = None  # monotonic time when silence began
    self._vad_fired = False  # prevents double-fire with max-duration timer
```

In `_callback()`, after frame append, track silence. **Critical: do NOT call stop methods from within `_callback`** — the callback holds `_lock` and `stop()` also acquires it (deadlock). Instead, set a flag and use a deferred Timer(0):

```python
if self._silence_threshold > 0 and self._silence_duration > 0:
    level = compute_level(indata[:, 0])
    if level < self._silence_threshold:
        if self._silence_start is None:
            self._silence_start = time.monotonic()
        elif (time.monotonic() - self._silence_start >= self._silence_duration
              and not self._vad_fired):
            self._vad_fired = True
            # Defer stop to avoid deadlock (callback holds _lock)
            threading.Timer(0, self._trigger_vad_stop).start()
    else:
        self._silence_start = None
```

Note: `_silence_start` is written only in `_callback` (single writer from PortAudio thread) and `_vad_fired` is a boolean flag (atomic on CPython). For extra safety, protect both under `_lock` reads in `_trigger_vad_stop`.

Add `_trigger_vad_stop()` that cancels the max-duration timer (prevents race/double-fire) then calls `_auto_stop()`:
```python
def _trigger_vad_stop(self) -> None:
    # Cancel max-duration timer to prevent double-stop race
    if self._timer:
        self._timer.cancel()
        self._timer = None
    log.info("Silence detected (%.1fs), auto-stopping", self._silence_duration)
    self._auto_stop()
```

The `_auto_stop()` → `stop()` → `on_auto_stop(wav_bytes)` chain then fires normally. In streaming mode this reaches `app._on_auto_stop` which calls `_finalize_streaming(from_auto_stop=True)` — the same path as the max-duration auto-stop (already handles stream thread join with 5s timeout).

**2.4 App integration (app.py)**

Pass VAD config to `AudioRecorder` only when mode is toggle:
```python
silence_threshold = 0.0
silence_duration = 0.0
if config.hotkey.mode == "toggle" and config.vad.enabled:
    silence_threshold = config.vad.silence_threshold
    silence_duration = config.vad.silence_duration

self.recorder = AudioRecorder(
    sample_rate=config.audio.sample_rate,
    max_duration=config.audio.max_duration,
    on_auto_stop=self._on_auto_stop,
    on_level=self._emit_level,
    silence_threshold=silence_threshold,
    silence_duration=silence_duration,
)
```

Pass `VadConfig` to `WhisperServerManager` (worker path — only when worker manages its own server, not in supervisor-launched mode):
```python
if config.whisper.managed and manage_server:
    self._server_manager = WhisperServerManager(config.whisper, vad_config=config.vad)
```

Add brief user feedback when VAD auto-stop fires — update `_on_auto_stop` to show overlay state + notify:
```python
def _on_auto_stop(self, wav_bytes: bytes) -> None:
    # (existing logic unchanged, add after state transition to PROCESSING)
    from samwhispers.notify import notify
    notify("SamWhispers", "Recording stopped (silence detected)")
```

**2.4b Supervisor integration (supervisor.py)**

The supervisor owns `WhisperServerManager` in the default deployment (worker runs with `--unmanaged-server`). The supervisor must also pass `VadConfig` when creating `WhisperServerManager`:

```python
# In _start_whisper (or equivalent):
from samwhispers.config import load_config
cfg = current_app_config(config_path)
self._whisper_mgr = WhisperServerManager(cfg.whisper, vad_config=cfg.vad)
```

This ensures VAD server-side flags are applied in the normal supervisor-managed deployment path, not just when the worker manages its own server.

**2.5 Restart logic (webserver.py)**

In `PUT /api/config`, check if VAD server fields changed to trigger whisper-server restart:
```python
whisper_restarted = (old_cfg.whisper != new_cfg.whisper) or _vad_server_changed(old_cfg.vad, new_cfg.vad)
```

Add helper:
```python
def _vad_server_changed(old: VadConfig, new: VadConfig) -> bool:
    """Server-side VAD fields that require whisper-server restart."""
    return (old.enabled != new.enabled or old.model_path != new.model_path
            or old.threshold != new.threshold
            or old.min_speech_duration_ms != new.min_speech_duration_ms
            or old.min_silence_duration_ms != new.min_silence_duration_ms
            or old.max_speech_duration_s != new.max_speech_duration_s
            or old.speech_pad_ms != new.speech_pad_ms
            or old.samples_overlap != new.samples_overlap)
```

**2.6 Model management (bootstrap.py)**

Extend `samwhispers-setup` to download the default Silero VAD ONNX model alongside the whisper model. URL: `https://github.com/ggerganov/whisper.cpp/raw/master/models/ggml-silero-vad.bin` (or current location in whisper.cpp repo).

**2.7 Config serialization (webconfig.py)**

Add `vad` to `to_toml_dict()`:
```python
data["vad"] = asdict(config.vad)
```

**2.8 Tests**

- `tests/test_config.py`: Test `VadConfig` parsing, validation (threshold bounds, file exists when enabled), round-trip.
- `tests/test_server.py`: Test `_build_cmd()` with and without VAD enabled — correct flags produced.
- `tests/test_audio.py`: Test silence detection — simulated frames below threshold for >duration triggers auto-stop; frames above threshold reset timer.

**Exit criteria**:
- [x] `VadConfig` dataclass with all server-side and client-side fields
- [x] `_build_cmd()` conditionally appends VAD flags when enabled
- [x] `AudioRecorder` tracks silence and fires auto-stop in toggle mode
- [x] Client-side VAD is inactive in hold mode
- [x] Client-side VAD works during streaming toggle sessions
- [x] VAD model downloadable via `samwhispers-setup`
- [x] Whisper-server restart triggered when server-side VAD fields change
- [x] Config round-trip: `build_config()` + `to_toml_dict()` handles VAD section
- [x] Tests pass for config, server flags, and silence detection
- [x] Update README.md VAD section with setup and config

**Implementation (2026-06-14, code: 71a5a7a)**
Added full VAD support. `VadConfig` dataclass carries server-side fields (threshold, speech/silence durations, pad, overlap) and client-side fields (silence_threshold, silence_duration). `WhisperServerManager._build_cmd()` conditionally appends `--vad`, `-vm`, `-vt` and related flags. `AudioRecorder` tracks silence in `_callback` with deferred `Timer(0)` for stop (deadlock prevention) and `_vad_fired` flag + timer cancellation (race prevention). Client-side VAD active only in toggle mode. Supervisor passes `VadConfig` to `WhisperServerManager` for the default deployment path. `webserver.py` adds `_vad_server_changed()` for whisper-server restart gating. `bootstrap.py` downloads Silero VAD model from huggingface.co/ggml-org/whisper-vad. Per-phase review deferred to Step 9: high-risk concurrency concerns were pre-addressed in plan review (deadlock, race, supervisor path all resolved at plan-time).

### Phase 3: Web UI — snippets and VAD pages [QA]

**Goal**: Config UI pages for managing snippets (key-value editor) and VAD settings.

**File scope**: `src/samwhispers/web/index.html`, `webserver.py`

**3.1 Snippets page**

Add a nav item (adjacent to Vocabulary in the nav):
```html
<li data-page="snippets">Snippets</li>
```

Build a key-value editor section:
- Table with columns: Trigger, Expansion, Delete button
- "Add snippet" button that appends a new row with empty inputs
- Checkbox: "Add snippets to vocabulary for better recognition" (`bias_recognition`)
- Save uses the existing `PUT /api/config` round-trip (merges snippet items into config dict)

**3.2 VAD page**

Add a nav item:
```html
<li data-page="vad">VAD</li>
```

Build the VAD settings section:
- Enable toggle
- Model path selector (shows detected models, download button if none found — reuse model download pattern)
- Server-side settings: threshold slider (0.0-1.0), min speech duration, min silence duration
- Client-side settings (shown only when mode=toggle): silence threshold slider, silence duration input
- Note: "Client-side auto-stop only applies in toggle mode"

**3.3 API meta extension (webserver.py)**

Extend `GET /api/meta` if needed to expose VAD model list (or reuse `GET /api/models` with a `type=vad` parameter).

**Exit criteria**:
- [x] Snippets page with key-value editor (add/remove/edit trigger→expansion pairs)
- [x] Snippets page shows `bias_recognition` checkbox
- [x] VAD page with enable toggle and all configurable fields
- [x] VAD page shows model download option
- [x] Client-side VAD section only shown when hotkey mode is "toggle"
- [x] Saving from either page correctly round-trips through `PUT /api/config`
- [x] UI changes persist after worker restart

**Implementation (2026-06-14, code: 88485d2)**
Added Snippets and VAD config pages to the web UI. Snippets page has key-value table editor with custom `collectSnippets()` logic, enabled toggle, and bias_recognition checkbox. VAD page has enable toggle, model path selector with download button (new `POST /api/vad/download` endpoint), server-side settings (threshold, durations, pad, overlap), and client-side auto-stop settings with toggle-mode-only note. Both use existing PUT /api/config round-trip. Per-phase review deferred to Step 9.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Snippet trigger contains filler word — eaten by filler removal | Low | Document limitation; snippets run after filler removal |
| Token budget overflow from snippet biasing | Low | Existing >100 word warning; `bias_recognition` toggle lets user disable |
| VAD model not downloaded when VAD enabled | Medium | Validate in `_validate()`; clear error; EX_CONFIG exit path handles it |
| 10s silence duration too aggressive for some users | Low | Configurable; documented default; only applies in toggle mode |
| VAD config in `[vad]` but whisper-server restart gated on `[whisper]` | Medium | Custom `_vad_server_changed()` check in PUT handler |
| Client-side VAD fires during brief pause | Low | 10s generous default; user can increase |
| Deadlock: audio callback holds lock, calls stop which re-acquires | High | Deferred stop via Timer(0); never call stop from callback |
| VAD/timer double-fire race (both auto-stops fire) | Medium | Cancel max-duration timer in `_trigger_vad_stop`; `_vad_fired` flag |
| Empty snippet expansion silently deletes trigger text | Medium | Validate: reject empty expansion strings at config load |
| Streaming preview shows unexpanded triggers | Low | Document: preview is raw; final is expanded |
| VAD model URL unverified | Low | Verify actual URL at implementation time; clear error on 404 |

## 7) Verification

```bash
# Unit tests
python -m pytest tests/test_postprocess.py tests/test_config.py tests/test_server.py tests/test_audio.py tests/test_app.py -v

# Full test suite
python -m pytest tests/ -v

# Lint + typecheck
python -m ruff check src/ tests/
python -m mypy src/
```

Manual verification:
- Define a snippet in config.toml, dictate the trigger phrase, verify expansion appears
- Enable VAD, verify whisper-server starts with `--vad` flag (check logs with `-v`)
- Set hotkey mode to "toggle", speak then go silent for >10s, verify recording auto-stops
- Confirm hold mode is unaffected by client-side VAD
- Open web UI, verify Snippets and VAD pages render and save correctly

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Add Snippets section (config format, usage, limitations) | 1 |
| `README.md` | Add VAD section (setup, server-side, client-side, config) | 2 |
| `config.example.toml` | Add `[snippets]` and `[vad]` example sections | 1, 2 |

## 9) Implementation Divergences from Plan

<Reserved — filled during implementation>

## Review Log

### 2026-06-14 -- Plan Review (via /qplan, High effort)

4 personas: Architect, Senior Engineer, End-user Advocate, Reliability Engineer. 36 findings total, 6 High. 10 auto-resolved (cycle 1).

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | High | Deadlock: VAD `_callback` holds `_lock`, `_trigger_vad_stop` → `stop()` re-acquires | Resolved — deferred via Timer(0), never call stop from callback |
| 2 | High | VAD/timer race: max-duration timer fires after VAD stop → double-stop with empty bytes | Resolved — cancel timer in `_trigger_vad_stop` + `_vad_fired` flag |
| 3 | High | Supervisor.py creates WhisperServerManager without VadConfig — flags never applied | Resolved — added §2.4b supervisor integration with VadConfig passing |
| 4 | High | Snippet config key collision: typos become snippet entries silently | Resolved — switched to nested `[snippets.items]` sub-table |
| 5 | High | No user feedback when VAD auto-stop fires | Resolved — notify user + overlay state change |
| 6 | High | Streaming VAD stop coordination with mid-tick session | Noted — existing `_finalize_streaming` join(5s) handles this; same path as max-duration. No plan change needed. |
| 7 | Medium | `_silence_start` data race (read/write from different threads) | Noted — single writer pattern (only callback writes); `_vad_fired` bool is atomic on CPython. Comment in plan acknowledges. |
| 8 | Medium | Streaming preview shows unexpanded triggers, then different text injected | Noted — documented as cosmetic; optionally expand in preview (implementer discretion) |
| 9 | Medium | `_vad_server_changed()` field-by-field ignores new fields | Noted — maintenance cost acknowledged; implementer may use subset comparison |
| 10 | Medium | Empty snippet expansion deletes trigger text silently | Resolved — validation added to reject empty expansion strings |
| 11 | Medium | VAD model URL unverified | Noted — verify at implementation time |
| 12 | Medium | Progressive mode + snippets = silent failure, no warning | Noted — add startup log warning when both enabled (implementer task) |
| 13 | Low | Phases share config.py/app.py — parallel annotation removed | Resolved — `[P:N]` annotations removed; phases are sequential |
| 14 | Low | `config.example.toml` not in file scopes | Resolved — added to both Phase 1 and 2 file scopes |
| 15 | Low | VAD page hides client-side section when mode≠toggle | Noted — show greyed-out (implementer UX decision) |

### 2026-06-14 -- Implementation Review (after Phase 1, persona: Senior engineer)

Implementation health: Yellow → Green (after fix).
4 findings (1 High, 0 Medium, 3 Low). 1 auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `pattern.sub(expansion, text)` treats expansion as re template — backslashes crash | Fixed — use `lambda m: expansion` (commit 107a0bd) |
| 2 | Low | No test for streaming-preview pipeline path with snippets | Noted — covered by Step 9 holistic review |
| 3 | Low | No test for `snippets.enabled = False` | Noted — covered by Step 9 |
| 4 | Low | No test for regex metacharacters in trigger | Noted — covered by Step 9 |
