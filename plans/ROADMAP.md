# SamWhispers Roadmap

> **Status**: Living document
> **Last Updated**: 2026-07-06
> **Purpose**: Track larger, not-yet-scheduled initiatives. Each item is a
> candidate, not a commitment; promote an item to a dated plan in `plans/`
> when it's picked up.

---

## 1. Pluggable transcription backends & hardware acceleration

> **Status**: Exploring
> **Scope**: Support alternative ASR engines (NVIDIA Parakeet) and hardware
> acceleration (CUDA today; Metal/Core ML, DirectML, Vulkan, OpenVINO, NPUs
> next) behind a single backend abstraction. ONNX Runtime is the unifying
> strategy for cross-vendor acceleration.

### Problem statement & desired outcomes

Today SamWhispers transcribes exclusively via a **CPU** `whisper.cpp` server.
That's portable but leaves a lot of speed and quality on the table:

- On machines with a GPU/NPU, inference could be 5-30x faster, enabling larger
  (more accurate) models at the same latency.
- The streaming feature is CPU-bound; it re-decodes overlapping windows, which
  is wasteful compared to a streaming-native model on an accelerator.
- A second engine (Parakeet) offers best-in-class English accuracy and true
  incremental (transducer) decoding.

Desired outcome: a user can choose an **engine** and an **acceleration
provider** appropriate to their hardware, defaulting to a sensible per-platform
choice, without the rest of the app caring which is active.

### Why Parakeet, CUDA, ONNX (recap)

- **CUDA** is a general win and applies to `whisper.cpp` itself (a build flag,
  not Parakeet-specific). Cheapest path to "faster + bigger models in real
  time". Needs an NVIDIA GPU.
- **Parakeet** (NVIDIA, FastConformer + TDT/RNNT/CTC via sherpa-onnx) is very
  fast, **streaming-native** (a much better fit for our streaming feature than
  chunked-Whisper), and top-tier on English. Trade-off: narrower language
  coverage (English-only or ~25 European languages) vs Whisper's 99 — a
  regression for our multilingual strength, so it must stay optional.
- **ONNX Runtime** is the unifying layer: one engine path, many **execution
  providers** (CUDA, TensorRT, CoreML, DirectML, OpenVINO, QNN, ROCm). Adopting
  ONNX for Parakeet also opens the door to most non-NVIDIA acceleration.

### Hardware acceleration matrix (the cross-vendor picture)

| Platform / hardware | Best accelerator | How to reach it |
|---|---|---|
| NVIDIA GPU (Win/Linux) | CUDA / TensorRT | whisper.cpp CUDA build; ONNX CUDA/TensorRT EP |
| Apple Silicon (Mac) | **Metal** (GPU) + **Core ML** (ANE) | whisper.cpp Metal + Core ML; ONNX CoreML EP |
| Any GPU on Windows | **DirectML** (vendor-agnostic, DX12) | ONNX DirectML EP |
| AMD GPU | ROCm/HIP (Linux) or **Vulkan** | whisper.cpp ROCm/Vulkan; ONNX ROCm EP |
| Intel GPU | SYCL/oneAPI or **OpenVINO** | whisper.cpp SYCL; ONNX OpenVINO EP |
| Intel/Qualcomm/Apple **NPU** (Copilot+ PCs) | OpenVINO (Intel), **QNN** (Qualcomm), Core ML (Apple ANE) | ONNX OpenVINO/QNN EP; Core ML |
| Cross-vendor GPU fallback | **Vulkan** | whisper.cpp Vulkan backend |
| CPU-only | optimized CPU (AVX/AVX-512), OpenVINO | current default |

**Key takeaways**
- For **Mac** (the most common non-NVIDIA laptop), the win is **Metal + Core
  ML** on our *existing* `whisper.cpp` engine — no Parakeet/ONNX needed.
- For **Windows non-NVIDIA**, **DirectML** (via ONNX) is the vendor-agnostic
  answer; **Vulkan** (whisper.cpp) is the cross-platform fallback.
- **ONNX Runtime EPs** cover the long tail (DirectML, CoreML, OpenVINO, QNN,
  ROCm) with one engine — strongest reason to invest in an ONNX path.

### Proposed approach

1. **Backend abstraction.** Generalize the existing streaming `StreamingEngine`
   ABC (`streaming.py`) into a broader transcription-backend interface used by
   both batch and streaming paths: `transcribe(audio) -> text` plus optional
   streaming/partials. Engines: `whisper_cpp` (current), `faster_whisper`
   (already present for streaming), `parakeet_onnx` (new), and potentially a
   generic `onnx_whisper`.
2. **Acceleration as a provider dimension.** Add an `acceleration`/`device`
   setting (`cpu`, `cuda`, `metal`, `coreml`, `directml`, `vulkan`, `openvino`,
   `auto`) resolved per engine. The supervisor already owns and (re)launches
   `whisper-server`; extend it to pass the right build/runtime flags and to
   manage any extra runtimes (mirroring OpenWhispr's CUDA manager).
3. **ONNX Runtime path.** Introduce an ONNX-based engine (covers Parakeet +
   most non-NVIDIA EPs). sherpa-onnx is the likely Parakeet runtime.
4. **Sensible defaults.** Detect hardware and pick: Mac -> Metal/Core ML;
   NVIDIA -> CUDA; Windows non-NVIDIA -> DirectML; else CPU. Always overridable
   in the config UI (extends the existing engine/model dropdowns).
5. **Keep multilingual on Whisper.** Parakeet is offered as an opt-in for
   English/European users; auto-detect + 99-language support stays on Whisper so
   our multi-language advantage is preserved.

### Phasing (rough)

- **Phase A — CUDA for whisper.cpp.** Managed CUDA build/launch of
  `whisper-server` via the supervisor + a config toggle. Contained, no new
  engine. Highest value-to-effort.
- **Phase B — Apple Metal/Core ML.** Surface the existing whisper.cpp Metal/
  Core ML support as a device option (big win for Mac laptops).
- **Phase C — ONNX Runtime engine + Parakeet.** New engine behind the backend
  abstraction; English/European only at first.
- **Phase D — Broaden ONNX EPs.** DirectML / OpenVINO / Vulkan / QNN as device
  options; hardware auto-detection and defaults.

### Risks & open questions

- Packaging/footprint: GPU runtimes and model formats are large and
  platform-specific; needs careful optional-dependency / download management
  (cf. our model-download feature and OpenWhispr's CUDA manager).
- Parakeet's narrower language set must never silently degrade multilingual
  users — guard with validation/UI.
- Streaming semantics differ per engine (transducer streaming vs chunked
  re-decode); the abstraction must accommodate both.
- Maintenance cost of multiple engines/EPs; consider standardizing on ONNX
  Runtime for everything except the whisper.cpp Metal/CUDA fast paths.

---

## 2. Local-or-cloud AI processing (offline LLM)

> **Status**: Exploring
> **Scope**: Run AI cleanup/translation through a **local LLM** (e.g. llama.cpp)
> as an alternative to the cloud providers, user-selectable per feature.

Today cleanup and translation require a cloud API (OpenAI/Anthropic) — the one
remaining cloud dependency in an otherwise local-first app. OpenWhispr runs AI
text processing fully offline via llama.cpp. Add a `provider = "local" | "openai"
| "anthropic" | ...` choice (per feature) with a managed local model
(download + run, mirroring whisper-server management). Keeps dictation 100%
offline end-to-end.

## 3. Cloud transcription option (BYOK)

> **Status**: Exploring
> **Scope**: Optional cloud transcription engine (OpenAI/Groq/etc.) as an
> alternative to local whisper.cpp, for speed or low-power devices.

We're local-only for transcription. Add a `cloud` engine behind the backend
abstraction (item 1) that POSTs audio to a transcription API with the user's
own key. Fits the same engine-selection UI; reuses the multi-provider plumbing
(item 4). Must remain opt-in (privacy).

## 4. Multi-provider management (BYOK)

> **Status**: Exploring
> **Scope**: Generalize hardcoded OpenAI/Anthropic into a managed list of AI
> providers usable across cleanup, translation, cloud transcription, and any
> future agent/actions.

OpenWhispr manages many providers (GPT-5, Claude, Gemini, Groq, local). Replace
our two-provider cleanup config with a provider registry (name, base URL, key,
model, type) that any AI feature can reference, surfaced in the config UI.
Foundation for items 2, 3, and a future actions/agent feature.

## 5. Meeting capture & diarization

> **Status**: Exploring (large; a distinct product direction)
> **Scope**: Record and transcribe meetings with speaker labels.

OpenWhispr's meeting suite — what we'd need to match it:

- **System/loopback audio capture** (record the call, not just the mic) —
  platform-specific (WASAPI loopback, Core Audio taps, PulseAudio monitor).
- **Auto-detect meetings** in Zoom / Teams / FaceTime and offer to record.
- **Speaker diarization**, local/on-device, ideally **live**.
- **Voice fingerprinting** that recognizes the same speaker across meetings.
- **Acoustic echo cancellation** for clean capture (OpenWhispr ships a native
  AEC helper).

This is a meetings product layered on the dictation core; sizeable, and only
worth it if we want to move beyond dictation.

## 6. Packaging: installers & auto-update

> **Status**: Exploring
> **Scope**: Native installers per OS and a built-in updater.

We're a Python daemon installed from source. OpenWhispr ships packaged
installers (electron-builder) and auto-update (`updater.js`). Candidates:
PyInstaller/Briefcase bundles or platform packages (.deb, .dmg, MSI/winget),
plus an update check against GitHub releases. Productization, not features, but
key for non-developer adoption.

## 7. Dictation intelligence & personalization (Wispr Flow gap analysis)

> **Status**: Exploring
> **Scope**: Make dictation itself smarter and more personal — real-time AI
> editing, voice editing commands, context/app awareness, mixed typing+voice,
> a learning dictionary, and whispered-speech support. Most of these
> build on the LLM provider work (items 2 & 4) and the streaming engine.

### 7.1 Real-time auto-edit while speaking

Apply AI cleanup **incrementally as you speak** (not a single post-pass), and
recognize **spoken self-corrections** inline ("no, make that Tuesday"). Builds
on the streaming path + a (local-or-cloud) LLM. Mode A (preview) is the natural
surface for showing live edits before injecting the final text.

### 7.2 Command mode

Voice commands that **edit already-written / selected text** — shorten,
lengthen, rephrase, change tone. Requires reading the current selection (or
recently injected text) and replacing it; LLM-driven. A distinct "command"
activation (separate hotkey or trigger word) vs normal dictation.

### 7.3 Context awareness

Detect the **focused application/window** and apply a matching formatting/tone
profile (email vs chat vs code). Needs platform-specific active-window
detection (X11 `_NET_ACTIVE_WINDOW`, Win32 `GetForegroundWindow`, macOS
Accessibility) plus user-defined per-app profiles in the config UI.

### 7.4 Multi-mode typing (voice + keyboard)

Let voice and typing mix: **read the surrounding text** in the target field and
continue mid-sentence / match its tone, rather than dumping a standalone block.
The hard part is reading the target field across arbitrary apps (accessibility
APIs / clipboard tricks) — a meaningful platform investment.

### 7.5 Personal dictionary with auto-learn

Extend the current (manual) vocabulary into a **learning dictionary**: when the
user corrects a transcription, learn new proper nouns and add them
automatically; manage entries (add / import / remove) in the config UI. Feeds
the existing `initial_prompt` biasing.

### 7.6 Whispered speech support

Improve recognition of **whispered / very quiet speech** for dictating
discreetly in public. Likely a mix of input normalization/gain, model choice,
and tuning; research needed to gauge what whisper.cpp / alternative models can
do here.

---

## 8. Single-key recording trigger

> **Status**: Candidate
> **Scope**: Allow a single modifier key (e.g. right-Alt, Fn, Caps Lock) as
> the recording hotkey, not just multi-key combos.
> **Origin**: OpenSuperWhisper competitive analysis (July 2026)

Both OpenSuperWhisper and OpenWhispr support single-modifier triggers (Left ⌘,
Right ⌥, Fn/Globe key). A single key is more ergonomic for hold-to-record —
the user presses one key with one finger rather than contorting for a combo.

Implementation notes:
- pynput can detect individual modifier key press/release events on all
  platforms.
- Needs disambiguation: a quick tap of Alt (to open menus) vs a hold (to
  record). Use a minimum hold duration threshold (~150ms) before entering
  recording state.
- Config: `hotkey.key = "right_alt"` or `hotkey.key = "caps_lock"` alongside
  the existing combo syntax.
- Must not break existing combo-key behavior — detect format and route
  accordingly.

---

## 9. Post-dictation CLI hooks

> **Status**: Candidate
> **Scope**: Run a user-defined shell command after each successful
> transcription, with the transcribed text available as input.
> **Origin**: OpenSuperWhisper competitive analysis (July 2026)

OpenSuperWhisper ships a CLI tool that supports post-record hooks — run any
command after dictation completes, piping the transcribed text. This enables
automation: logging, sending to APIs, triggering workflows, appending to files.

Implementation notes:
- New config section: `[hooks]` with `post_transcribe = "command"`.
- The command receives the final text via stdin (or as `$1` / `%1`).
- Environment variables: `SW_TEXT`, `SW_LANGUAGE`, `SW_DURATION_MS`,
  `SW_APP` (focused app, if context-awareness is present).
- Run async (fire-and-forget) so it doesn't block the next dictation.
- Timeout + log stderr on failure; never let a broken hook crash the app.
- Optional: `post_cleanup` hook (runs after AI processing, with both raw and
  cleaned text).

---

## 10. Movable/configurable overlay position

> **Status**: Candidate
> **Scope**: Let users configure where the on-screen recording indicator
> appears — cursor-following, screen corners, center, or a fixed offset.
> **Origin**: OpenSuperWhisper / OpenWhispr competitive analysis (July 2026)

OpenSuperWhisper offers cursor-following, edge-docked, and notch/Dynamic Island
positions. OpenWhispr's overlay is draggable. Our fixed bottom-center placement
doesn't suit all workflows (e.g. the overlay may obscure the text you're
dictating into).

Config: `overlay.position = "bottom-center" | "top-right" | "cursor" | ...`
plus an optional pixel offset. The overlay module already manages a Tk window;
extending its geometry logic is straightforward.

---

## 11. Microphone device picker

> **Status**: Candidate
> **Scope**: Let users select a specific audio input device from the web config
> UI instead of always using the system default.
> **Origin**: OpenSuperWhisper / OpenWhispr competitive analysis (July 2026)

Both competitors offer mic selection (menu bar in OpenSuperWhisper, settings in
OpenWhispr). Users with USB mics, Bluetooth headsets, or multiple inputs need
this.

Implementation notes:
- `sounddevice.query_devices()` lists available inputs — expose via a
  `/api/devices` endpoint.
- New config: `[audio] device = "default"` or a device name/index.
- Web UI: dropdown populated from the API, with a "Test" button that captures
  1s and shows the level.
- Hot-reload: switching devices should not require a full restart.

---

## 12. History context metadata

> **Status**: Candidate
> **Scope**: Record which application/window was focused during each dictation,
> alongside the existing timestamp and duration.
> **Origin**: OpenSuperWhisper competitive analysis (July 2026)

OpenSuperWhisper's history shows the source app, website, duration, and model
used for each entry — and lets you re-transcribe with a different model. Adding
context metadata makes history more useful for review and debugging.

Fields to capture:
- `app_name` — the focused application (via `GetForegroundWindow` title on
  Windows, `xdotool getactivewindow` on X11).
- `model` — which whisper model was used.
- `engine` — which transcription engine (once multi-engine lands).
- `language` — detected or forced language.

Store in the existing SQLite history table (new columns). Surface in the web UI
history tab with filter/search by app.

---

## 13. AI agent / command mode

> **Status**: Candidate
> **Scope**: Detect a trigger phrase (e.g. "Hey Sam, ...") at the start of a
> dictation and route the text to an AI model for transformation instead of
> raw injection.
> **Origin**: OpenWhispr competitive analysis (July 2026)
> **Depends on**: Items 2 & 4 (multi-provider / local LLM)

OpenWhispr's agent mode lets users address a named AI assistant during
dictation ("Hey Assistant, format this as a bullet list"). The AI processes the
instruction and returns transformed text. This turns dictation into a hands-free
AI command interface without a separate hotkey.

Implementation notes:
- Config: `[agent] enabled = true`, `name = "Sam"`,
  `provider = "openai" | "anthropic" | "local"`.
- Detection: after transcription, check if text starts with
  `"hey {name},"` (case-insensitive). If matched, strip the prefix and send the
  remainder to the AI provider with a system prompt instructing transformation.
- The agent name is auto-added to the vocabulary list (biases Whisper toward
  recognizing it).
- Fallback: if AI fails, inject the original text (minus the trigger prefix)
  with a notification.
- Future: a dedicated agent hotkey (separate from dictation hotkey) that always
  routes to the AI, no trigger phrase needed.

---

## 14. Audio file transcription (batch mode)

> **Status**: Candidate
> **Scope**: Transcribe existing audio files (drag & drop or file picker) via
> the web UI or CLI, without requiring live microphone recording.
> **Origin**: OpenSuperWhisper / OpenWhispr competitive analysis (July 2026)

Both competitors support transcribing pre-recorded audio. OpenSuperWhisper has
drag & drop with queue processing; OpenWhispr has an upload UI in its Notes
system.

Implementation notes:
- Web UI: an "Upload" button in the history tab (or a new "Transcribe File"
  tab). Accept common formats: WAV, MP3, M4A, FLAC, OGG, WEBM.
- Convert to 16kHz mono WAV before sending to whisper-server (use ffmpeg or
  the `soundfile` library).
- CLI: `samwhispers transcribe <file>` — output text to stdout, or `--json`
  for structured output with timestamps.
- Queue: for multiple files, process sequentially with progress indication.
- Store results in history with `source = "file"` and the filename.
- Long files: consider chunking (split at silence boundaries) to stay within
  whisper-server's memory/timeout limits.

---

## 15. Per-app engine/profile rules

> **Status**: Candidate
> **Scope**: Automatically switch the transcription engine, model, language, or
> AI cleanup profile based on the currently focused application.
> **Origin**: OpenSuperWhisper competitive analysis (July 2026)
> **Depends on**: Items 1 (multi-engine) & 12 (app context detection)

OpenSuperWhisper lets users bind a specific model to an app or website — it
switches automatically when you dictate there. E.g. use a fast small model in
Slack, a large accurate model in your email client, enable translation only in
a specific app.

Implementation notes:
- Config: `[[rules]]` array with `app_pattern`, `engine`, `model`, `language`,
  `cleanup_enabled`, `translation_enabled` fields.
- App detection reuses the platform code from item 12 (history context).
- Rule matching: glob or regex on window title / process name.
- UI: a "Rules" section in the web config with add/edit/delete.
- Fallback: if no rule matches, use the global defaults.

---

## 16. OS credential storage for API keys

> **Status**: Candidate
> **Scope**: Store provider API keys in the OS keychain/credential vault
> instead of plaintext config.toml.
> **Origin**: OpenSuperWhisper / OpenWhispr competitive analysis (July 2026)

OpenSuperWhisper stores secrets in macOS Keychain; OpenWhispr uses `.env` with
restrictive permissions. Our plaintext TOML is the weakest approach.

Implementation notes:
- Use the `keyring` Python library (cross-platform: Windows Credential Vault,
  macOS Keychain, Linux Secret Service/kwallet).
- Config.toml stores a sentinel value (e.g. `api_key = "@keyring"`) indicating
  the real key is in the OS store.
- Web UI: the existing redacted key display + password field continues to work;
  on save, write to keyring instead of TOML.
- Migration: on first run with the new version, if a plaintext key exists in
  TOML, offer to migrate it to keyring and replace with the sentinel.
- Fallback: if `keyring` is unavailable (headless Linux without a secret
  service), keep the existing plaintext behavior with a warning.

---

## Other roadmap candidates (unscheduled)

- **Insertion context pre-prompt** — feed surrounding text / app context into
  the AI cleanup prompt so output matches the insertion point's tone/style.
- **Fine-tuned model support** — allow loading user-fine-tuned Whisper models
  (custom vocabulary domains, accent adaptation).
- **Language-code normalization** (e.g. `zh -> zh-CN`) once non-Whisper engines
  are in play, to map between engine code sets.
- **Simple "preferred language" mode** as an alternative to the configured
  list + cycle hotkey, for casual multilingual users.
- **MCP server / public API** to expose dictation to AI assistants.

---

## Architecture & patterns to adopt

> **Origin**: Competitive analysis of OpenSuperWhisper & OpenWhispr (July 2026)
> **Purpose**: Cross-cutting patterns to apply as the codebase evolves, not
> standalone features.

### A. Remote-engine local fallback

When a remote/cloud transcription engine is configured, automatically fall back
to the local whisper.cpp engine if the remote server is unreachable (network
error, timeout, 5xx). OpenSuperWhisper does this transparently. Apply when
implementing item 3 (cloud BYOK transcription).

### B. Paste fallback chain (Linux)

OpenWhispr implements a robust paste fallback: native binary → wtype (Wayland)
→ ydotool → xdotool → manual paste prompt. SamWhispers currently relies on
`xclip` + pynput only. As we move toward broader Linux support (Wayland), adopt
a tiered fallback in `inject.py` that tries multiple methods and picks the
first that works.

### C. Engine benchmark data in model selection UI

OpenSuperWhisper shows a per-language WER/speed/score comparison table for all
engines. When multi-engine lands (item 1), surface benchmark data (measured or
reference) alongside the model dropdown in the web config to help users make
informed choices.

### D. Manager lifecycle pattern

OpenWhispr organizes its main process into explicit manager classes
(WhisperManager, ClipboardManager, HotkeyManager, etc.) each with init/start/
stop lifecycle methods. SamWhispers already has a reasonable module split, but
as complexity grows (multiple engines, local LLM, hooks, rules), consider
formalizing a manager registry with ordered startup/shutdown. This aids
testability and hot-reload of individual subsystems.

### E. Model re-transcription from history

OpenSuperWhisper lets users re-run a past dictation through a different model
directly from the history UI. Once multi-engine is in place and we store the
original audio path (or a short audio buffer) alongside history entries,
offering "re-transcribe with model X" is a high-value, low-cost addition to
the history tab.
