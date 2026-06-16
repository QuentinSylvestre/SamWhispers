# Whisper-Streaming Sentence-Boundary Buffer Trimming

> **Date**: 2026-06-16
> **Status**: In Progress  <!-- Status lifecycle: Exploring → Draft → In Progress → Complete -->
> **Scope**: Replace fixed 30s sliding window in streaming transcription with sentence-boundary buffer trimming (Whisper-Streaming algorithm)
> **Estimated effort**: 2-3 days

---

## Intent

### Problem statement & desired outcomes

The streaming transcription feature currently uses a fixed 30-second sliding window. After 30s of recording, word positions drift, agreement stalls, and in preview mode early speech is lost entirely. This makes streaming unreliable for any recording exceeding 30 seconds.

The Whisper-Streaming algorithm (Macháček et al. 2023) solves this by trimming the audio buffer at confirmed sentence boundaries rather than at arbitrary time offsets. The buffer stays sentence-length (~5-15s) regardless of total recording duration, enabling unbounded-length streaming with bounded memory and latency.

**Desired outcome**: Streaming transcription works correctly for recordings of any length in both preview and progressive modes, with the buffer trimmed at natural sentence boundaries.

### Success criteria

1. Streaming transcription produces correct, complete output for recordings exceeding 60 seconds in both preview and progressive modes (no lost words, no agreement stalls)
2. Audio buffer memory stays bounded at ~sentence-length during streaming (not growing with total recording duration)
3. Per-tick CPU cost is O(sentence_length), not O(total_recording_duration)
4. Word-level timestamps from whisper.cpp (`verbose_json`) and faster-whisper (`word_timestamps=True`) are parsed and used for trim decisions
5. Confirmed text context (~100 words) is passed as prompt to subsequent decodes for style/terminology continuity
6. If timestamps are unavailable (old whisper.cpp build), streaming fails loudly with a clear error message and falls back to batch mode
7. The `window_seconds` config acts as a safety ceiling for pathologically long sentences (not the primary trim mechanism)

### Scope boundaries & non-goals

**In scope**:
- New `TranscribeResult` return type from engines (text + word timestamps)
- `LocalAgreement` extended to track timestamps per committed word
- Sentence-boundary detection: trim when a committed word ends with sentence punctuation AND the next word is also committed (configurable `min_words_after_sentence`, default 1)
- `AudioRecorder.trim_front(n_samples)` method to discard frames from the buffer head
- Per-trim prompt update (last ~100 committed words + existing vocab/accent)
- Cumulative sample offset tracking for timestamp-to-absolute mapping
- Existing tests updated, new integration tests for long recordings

**Non-goals**:
- DTW-based timestamps (cross-attention accuracy is sufficient for sentence detection)
- Graceful long-sentence handling (roadmap item — current behavior: ceiling kicks in)
- Changes to batch (non-streaming) transcription path
- Real-time word-by-word display in preview mode overlay (preview still shows full hypothesis per tick)

**Roadmap item**: Graceful handling when a single sentence exceeds `window_seconds` — instead of hard-trimming mid-sentence, explore partial-sentence trim with overlap, or force a mid-sentence trim at a clause boundary (comma). Deferred to a future iteration.


## 1) Current State

- `streaming.py:140-178` — `StreamingEngine` ABC with `transcribe(audio, sample_rate) -> str`. Only returns text.
- `streaming.py:149-155` — `ChunkedEngine.transcribe()` calls `numpy_to_wav` then `WhisperClient.transcribe(wav_bytes)`.
- `transcribe.py:55-62` — Posts to `/inference` with `response_format: "json"`, parses `result.get("text", "")`. No timestamps parsed.
- `streaming.py:82-138` — `LocalAgreement` stores `committed: list[str]` — plain words, no timestamp metadata.
- `streaming.py:215-253` — `StreamingSession.tick()` uses `audio.size > max_samples` (fixed window). Sets `word_offset = len(committed)`.
- `audio.py:225-244` — `snapshot(max_samples)` walks frames backwards. No front-trim capability. `_frames` only cleared at `start()`/`stop()`.
- `app.py:133-160` — `_build_prompt()` returns static vocabulary+accent string. Never updated with confirmed text.
- `config.py:344-355` — `StreamingConfig` has `window_seconds: float = 30.0`.

## 2) Goal

Replace the fixed 30s sliding window with sentence-boundary buffer trimming: the audio buffer is trimmed at confirmed sentence-ending timestamps, keeping it sentence-length. Whisper receives rolling prompt context from confirmed text for inter-sentence continuity.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| When is a sentence boundary "confirmed"? | Committed word with sentence-ending punct + next word also committed | Wait 5-10s; aggressive trim at any period | 1-word-after is the Whisper-Streaming paper's approach; Whisper rarely removes a period it already emitted; LocalAgreement provides 2-hypothesis confirmation |
| Engine return type | Change `transcribe() -> TranscribeResult` (text + words with timestamps) | Add separate `transcribe_timed()` method | Single consumers (tick/finalize), no external API break, simpler |
| Buffer trimming mechanism | `AudioRecorder.trim_front(n_samples)` — actually discard frames | Track offset, never trim | Real memory savings, O(sentence) snapshot |
| Prompt context | Last ~100 committed words + existing vocab/accent, updated per trim | Static prompt only; full 200 words | 100 words ≈ 130-150 tokens, leaves room for vocab+accent within 224-token budget |
| Timestamp accuracy | Cross-attention (default, no `--dtw`) | DTW-based (requires model flag) | ~50-100ms accuracy sufficient for sentence boundaries (~200-500ms gaps) |
| Missing timestamps behavior | Fail loudly, disable streaming, fall back to batch | Graceful degradation to fixed window | User explicitly chose "fail loudly" — clear error > silent degradation |
| `window_seconds` role | Safety ceiling for long sentences (fallback) | Remove entirely | Prevents unbounded growth on pathological single-sentence recordings |
| Configurable confirmation depth | `min_words_after_sentence` config, default 1 | Hardcoded | Lets cautious users increase to 2-3 |

## 4) External Dependencies & Costs

### Required external changes

None — whisper.cpp already supports `verbose_json`; no infrastructure changes needed.

### Cost impact

None — reduces CPU/memory usage. No new services or API costs.

## 5) Implementation Phases

### Phase 1: Structured engine return type, timestamp parsing, and prompt update [QA] [P:2]

**Goal**: Change `StreamingEngine.transcribe()` to return `TranscribeResult` (text + word timestamps). Add `update_prompt()` to the engine ABC. Update both engines and the whisper client.

**File scope**: `src/samwhispers/streaming.py`, `src/samwhispers/transcribe.py`, `tests/test_streaming.py`, `tests/test_transcribe.py`

**Changes**:

```python
# streaming.py — new dataclasses
@dataclass
class WordTimestamp:
    word: str
    start: float  # seconds relative to audio chunk start
    end: float

@dataclass
class TranscribeResult:
    text: str
    words: list[WordTimestamp]
```

- `StreamingEngine` ABC: change `transcribe(...) -> TranscribeResult`; add abstract `update_prompt(prompt: str)`.
- `WhisperClient`: add `transcribe_verbose(wav_bytes) -> TranscribeResult` that posts with `response_format=verbose_json`, parses `words` array from segments. Validate that each word has numeric `start`/`end` (not just presence of `words` array — `no_timestamps` flag can produce words without timing). Skip zero-duration punctuation-only tokens.
- `ChunkedEngine.transcribe()` → returns `TranscribeResult` using `client.transcribe_verbose()`. `update_prompt(prompt)` → `self._client.prompt = prompt`.
- `FasterWhisperEngine.transcribe()` → passes `word_timestamps=True`, builds `TranscribeResult` from segment word data. `update_prompt(prompt)` → `self._prompt = prompt`.
- Fail loudly: if verbose_json response has no `words` array or words lack `start`/`end`, raise `StreamingUnavailableError`.
- Update `ScriptedEngine` in tests to return `TranscribeResult`.

**Exit criteria**:
- [x] `TranscribeResult` and `WordTimestamp` dataclasses defined
- [x] `StreamingEngine.update_prompt(prompt: str)` abstract method added
- [x] `WhisperClient.transcribe_verbose()` posts `response_format=verbose_json` and parses word timestamps
- [x] Validates word timestamps are numeric (handles `no_timestamps` server flag)
- [x] `ChunkedEngine` returns `TranscribeResult`; implements `update_prompt`
- [x] `FasterWhisperEngine` returns `TranscribeResult` with `word_timestamps=True`; implements `update_prompt`
- [x] Engine raises `StreamingUnavailableError` if timestamps unavailable
- [x] Zero-duration/punctuation-only tokens normalized in parse
- [x] Existing streaming tests updated to use new return type
- [x] New tests for timestamp parsing (mock verbose_json responses)

#### Implementation (2026-06-16, code: a8ea8ab)

Added `WordTimestamp` and `TranscribeResult` dataclasses to `streaming.py`, changed `StreamingEngine.transcribe()` to return `TranscribeResult`, added abstract `update_prompt(prompt)` method to the ABC, and implemented both in `ChunkedEngine` (delegates to new `WhisperClient.transcribe_verbose()`) and `FasterWhisperEngine` (passes `word_timestamps=True`, builds result from segment word data). Added `transcribe_verbose()` to `WhisperClient` that posts with `response_format=verbose_json`, validates numeric timestamps, skips zero-duration punctuation-only tokens, and raises `StreamingUnavailableError` (new exception in `exceptions.py`) when timestamps are unavailable. Updated `StreamingSession.tick()` and `finalize()` to unpack `TranscribeResult`. Updated `ScriptedEngine` and all existing streaming tests to use the new return type, and added 6 new tests for verbose timestamp parsing (success, multi-segment, zero-duration filtering, non-numeric validation, no-words error, empty text).

### Phase 2: Audio buffer front-trimming with deque [QA] [P:1]

**Goal**: Convert `AudioRecorder._frames` to `collections.deque` and add `trim_front(n_samples)` to discard confirmed audio from the buffer head.

**File scope**: `src/samwhispers/audio.py`, `tests/test_audio.py`

**Changes**:

```python
# audio.py — change _frames type
from collections import deque

self._frames: deque[np.ndarray] = deque()

# New method
def trim_front(self, n_samples: int) -> int:
    """Discard the first n_samples from the buffer. Returns actual samples trimmed."""
    with self._lock:
        if not self._recording:
            return 0
        trimmed = 0
        while self._frames and trimmed < n_samples:
            frame = self._frames[0]
            if trimmed + frame.size <= n_samples:
                self._frames.popleft()  # O(1) with deque
                trimmed += frame.size
            else:
                cut = n_samples - trimmed
                self._frames[0] = frame[cut:]
                trimmed = n_samples
        return trimmed
```

- Convert `_frames` from `list` to `deque` (O(1) popleft instead of O(n) pop(0)).
- Update all `_frames` access (`append` stays the same on deque; `list(self._frames)` in `snapshot` works with deque).
- Guard: `if not self._recording: return 0` at top of `trim_front`.

**Exit criteria**:
- [x] `_frames` converted to `collections.deque`
- [x] `trim_front(n_samples)` method added, returns actual samples trimmed
- [x] O(1) popleft for full-frame removal
- [x] Handles partial frame at boundary
- [x] Thread-safe (operates under `_lock`)
- [x] Guard against trim after recording stops
- [x] Unit tests: trim full frames, trim partial, trim zero, trim more than available
- [x] Existing snapshot/stop tests still pass with deque

#### Implementation (2026-06-16, code: a8ea8ab)

Converted `AudioRecorder._frames` from `list[np.ndarray]` to `collections.deque[np.ndarray]` for O(1) front removal, updated all reset sites (`__init__`, `start()`, `stop()`) to use `deque()`, captured frames as `list()` in `stop()` before reset, and added the `trim_front(n_samples: int) -> int` method that discards confirmed audio from the buffer head under `_lock` with proper handling of full-frame popleft, partial frame slicing at boundaries, and a guard returning 0 when not recording. Added 6 unit tests covering full-frame trim, partial-frame trim, zero trim, over-trim, not-recording guard, and snapshot-after-trim verification.

### Phase 3: Sentence-boundary trimming in StreamingSession [QA]

**Goal**: Replace fixed-window logic with sentence-boundary buffer trimming. StreamingSession owns the recorder reference, performs snapshot+trim atomically. Tracks timestamps in LocalAgreement, detects sentence boundaries, trims audio, updates prompt.

**Depends on**: Phase 1 + Phase 2

**File scope**: `src/samwhispers/streaming.py`, `src/samwhispers/app.py`, `src/samwhispers/config.py`, `tests/test_streaming.py`

**Design notes (from review)**:
- StreamingSession takes a `recorder` reference (or abstract buffer interface) in its constructor. `tick()` calls `recorder.snapshot()` internally and performs trim atomically — no race between snapshot and trim.
- `_stream_loop` no longer passes audio to tick; it just calls `session.tick()`.
- Timestamps stored in `committed_timestamps` are RELATIVE to current buffer start (what the engine returns). Cumulative offset tracked separately for word_offset alignment.
- Trim target: `int(end_time * sample_rate)` (relative to current buffer), NOT absolute.
- After trim: `_cumulative_trimmed_seconds += end_time`.
- Sentence boundary regex uses capitalization check + abbreviation blocklist (not naive `[.!?]$`).
- Minimum buffer duration after trim: don't decode if remaining buffer < 2s (prevents Whisper hallucination on short audio).

**Changes**:

1. **StreamingSession constructor** — add `recorder` parameter (abstract buffer with `snapshot()`/`trim_front()` interface). Remove audio from `tick()` signature:
   ```python
   def tick(self) -> str:
       audio = self._recorder.snapshot()
       # ... rest of tick logic
   ```

2. **LocalAgreement extended** — store `WordTimestamp` alongside committed words:
   ```python
   self.committed_timestamps: list[WordTimestamp] = []
   ```
   `update()` accepts `words: list[WordTimestamp]` (full structured data). `commit_all()` also accepts timestamps.

3. **Sentence-boundary detection** — smarter than `[.!?]$`:
   ```python
   _ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "st", "jr", "sr", "inc", "ltd", "corp", "etc", "vs", "prof"}
   _SENTENCE_END_RE = re.compile(r"[.!?]$")

   def _is_sentence_boundary(self, word: str, next_word: str) -> bool:
       if not _SENTENCE_END_RE.search(word):
           return False
       # Abbreviation check: "Dr." followed by a name is not a boundary
       stem = re.sub(r"[.!?]+$", "", word).lower()
       if stem in _ABBREVIATIONS:
           return False
       # Number check: "3.14" is not a boundary
       if re.match(r"\d", stem):
           return False
       # Capitalization heuristic: next word starting with uppercase suggests new sentence
       if next_word and next_word[0].isupper():
           return True
       # Sentence-ending after a lowercase word (e.g., "...done. how") — still a boundary
       return True
   ```

4. **Trim execution in tick()** — after agreement update, check for confirmed sentence boundary. If buffer remaining after trim >= 2s (minimum buffer duration):
   - Call `self._recorder.trim_front(trim_samples)` directly (atomic with snapshot — same thread, no race)
   - Update `_cumulative_trimmed_seconds += end_time_of_boundary_word`
   - Update engine prompt with last ~100 committed words + base prompt
   - Reset LocalAgreement's offsets

5. **Minimum buffer check**: if remaining audio after proposed trim < `2 * sample_rate` samples (2s), defer the trim to the next tick (buffer will have grown).

6. **Prompt update**: `self._engine.update_prompt(context_words + " " + self._base_prompt)` where context_words = last 80 committed words (80 words for multilingual safety margin, ~100-120 tokens).

7. **App.py** — `_start_stream()` passes recorder to session:
   ```python
   session = StreamingSession(
       self._stream_engine,
       self.config.audio.sample_rate,
       recorder=self.recorder,
       ...
   )
   ```
   `_stream_loop` calls `session.tick()` with no audio parameter.

8. **Window ceiling fallback** — if no sentence boundary detected and `snapshot()` returns audio exceeding `window_seconds`, use existing window-trim + word_offset behavior (no buffer trim, just decode the tail).

9. **Config** — add `min_words_after_sentence: int = 1` to `StreamingConfig`.

10. **Language note**: document that sentence-boundary trimming works best with explicit language codes. With `language=auto`, detected language may reset between trims.

**Exit criteria**:
- [ ] `StreamingSession` takes recorder reference; `tick()` has no audio parameter
- [ ] `LocalAgreement` tracks `committed_timestamps` parallel to `committed`
- [ ] `commit_all` accepts and stores timestamps
- [ ] Sentence-boundary detection uses abbreviation blocklist + capitalization heuristic
- [ ] Minimum 2s buffer after trim (defers trim if too short)
- [ ] Buffer trim executes via `recorder.trim_front()` atomically within tick
- [ ] `_cumulative_trimmed_seconds` tracked; uses relative timestamps for trim, absolute for word_offset
- [ ] Prompt updated per-trim (80 committed words + base prompt)
- [ ] `window_seconds` ceiling still works as fallback for long sentences
- [ ] `min_words_after_sentence` configurable (default 1)
- [ ] `finalize()` does not trim — just commits remaining (recording is over)
- [ ] Integration test: 60s+ simulated recording trims at sentence boundaries
- [ ] Integration test: 3+ consecutive trims with monotonically increasing absolute timestamps
- [ ] Integration test: window ceiling fires on a single long sentence
- [ ] Integration test: progressive mode with multiple trims, no gaps or duplicates
- [ ] Integration test: abbreviation "Dr. Smith" does NOT trigger trim
- [ ] Update README.md streaming section to document behavior change
- [ ] Update config.example.toml with `min_words_after_sentence`

### Phase 4: Fail-loud and batch fallback [QA]

**Goal**: When timestamps are unavailable, streaming fails loudly and falls back to batch mode.

**File scope**: `src/samwhispers/streaming.py`, `src/samwhispers/app.py`, `tests/test_streaming.py`

**Changes**:

- `StreamingUnavailableError` defined in `streaming.py`.
- In `ChunkedEngine.transcribe()`: raise if verbose_json response has no `words` array or words lack numeric `start`/`end`.
- In `app.py` `_stream_loop`: catch `StreamingUnavailableError` on first tick, log clearly, disable streaming for this session, notify user via overlay/notification, fall back to batch mode (put remaining audio on work queue).

**Exit criteria**:
- [ ] `StreamingUnavailableError` defined
- [ ] Engine raises it when timestamps missing or malformed
- [ ] App catches it, logs clear message, disables streaming for session
- [ ] User notified (overlay or notification)
- [ ] Remaining audio captured and processed via batch path
- [ ] Test: mock response without words → error raised
- [ ] Test: app handles error gracefully (falls back to batch)

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Whisper removes period between consecutive hypotheses (false trim) | Words before trim lost | 1-word-after confirmation + LocalAgreement 2-hypothesis agreement = 3+ ticks of stability before trim |
| Timestamp inaccuracy causes trim at wrong sample | Audio discontinuity, brief glitch | Cross-attention accuracy ~50-100ms; sentence gaps are 200-500ms; 100ms error is harmless |
| Long single sentence exceeds window ceiling | Agreement stalls (current behavior) | Documented as roadmap item; ceiling fallback preserves existing behavior |
| Prompt context exceeds token limit | Whisper truncates silently | Cap at 100 words (~130-150 tokens); vocabulary + accent adds ~30-50; total well under 224 |
| `trim_front` under lock blocks audio callback | Brief audio dropout | Trimming is O(n_frames_trimmed) — typically 5-10 pops, <1ms |

## 7) Verification

```bash
python -m pytest tests/test_streaming.py tests/test_transcribe.py -v
python -m pytest tests/test_audio.py -v
python -m ruff check src/samwhispers/ tests/
python -m mypy src/samwhispers/streaming.py src/samwhispers/audio.py src/samwhispers/transcribe.py
```

Manual: run `samwhispers` with streaming enabled, speak for >60s, verify words appear continuously without stalls or data loss.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Update streaming section: remove "30s window" limitation language, document sentence-boundary trimming behavior, add `min_words_after_sentence` config | 3 |
| `config.example.toml` | Add `min_words_after_sentence = 1` to `[streaming]` section | 3 |

## 9) Implementation Divergences from Plan

<Reserved -- filled during implementation>

## Review Log

### 2026-06-16 -- Plan Review Cycle 1 (Architect + Senior Engineer + Domain Expert: Streaming ASR)

10 findings (3 High, 6 Medium, 4 Low). All auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `update_prompt()` method missing from engine ABC — Phase 3 calls it but no engine defines it | Added to Phase 1: `update_prompt(prompt: str)` abstract method + implementations on both engines |
| 2 | High | Snapshot/trim race condition — `_stream_loop` snapshots while trim may fire concurrently | Redesigned: Session owns recorder reference, `tick()` calls snapshot+trim atomically (same thread) |
| 3 | High | Naive `[.!?]$` regex false-triggers on "Dr.", "3.14", "U.S." — causes mid-sentence trims | Added abbreviation blocklist + capitalization heuristic in Phase 3 |
| 4 | Medium | Cumulative offset conflation — plan mixed relative (for trim) and absolute (for word_offset) | Clarified: trim uses relative `int(end_time * sample_rate)`, cumulative offset separate for alignment |
| 5 | Medium | `commit_all` needs timestamps parameter to maintain parallel lists | Added to Phase 3: `commit_all` accepts timestamps, keeps both lists synchronized |
| 6 | Medium | `tick()` signature incompatible with new recorder-owns-snapshot design | Redesigned: `tick()` takes no audio param, calls `self._recorder.snapshot()` internally |
| 7 | Medium | Hallucination on short post-trim buffers (<2s) — known Whisper quirk | Added minimum buffer duration check (2s) before decoding after trim |
| 8 | Medium | Language detection resets on each decode with `language=auto` | Documented as limitation; recommend explicit language codes for streaming |
| 9 | Medium | `_frames.pop(0)` is O(n) on a list — slow for many frames | Phase 2 now converts `_frames` to `collections.deque` (O(1) popleft) |
| 10 | Low | Missing test for timestamp continuity across multiple consecutive trims | Added exit criterion: 3+ trim integration test with monotonic absolute timestamps |

### 2026-06-16 -- Implementation Review (after Phase 1, persona: Senior engineer, Reliability engineer, Maintainability reviewer, Performance engineer)

Implementation health: Green.
5 findings (0 High, 1 Medium, 3 Low, 1 Info). Effort: High.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | `transcribe_verbose()` has no retry logic; stream loop 5-error tolerance is the only safety net | User: accepted — stream loop tolerance sufficient; retry adds latency |
| 2 | Low | `PUNCT_ONLY_RE` duplicated between streaming.py and transcribe.py | Fixed — transcribe.py now imports from streaming.py |
| 3 | Low | `SizeTrackingEngine` in tests missing `update_prompt()` stub | Fixed — added one-line stub |
| 4 | Low | No monotonic timestamp validation in `transcribe_verbose()` | User: accepted — whisper.cpp guarantees monotonic; worst case is under-trim |
| 5 | Info | Edge case: all-punctuation transcription raises StreamingUnavailableError | Acceptable — whisper.cpp never produces this in practice |

### 2026-06-16 -- Implementation Review (after Phase 2, persona: Senior engineer, Reliability engineer, Performance engineer, Maintainability reviewer)

Implementation health: Green.
3 findings (0 High, 0 Medium, 3 Low). Effort: High.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Low | Older tests assign `_frames` as plain list instead of deque | Fixed — converted all test assignments to deque |
| 2 | Low | `trim_front` does not guard against negative `n_samples` | Fixed — added `n_samples <= 0` early return |
| 3 | Low | No concurrent trim stress test | User: accepted — same lock pattern proven by existing concurrency test |
