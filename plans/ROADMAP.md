# SamWhispers Roadmap

> **Status**: Living document
> **Last Updated**: 2026-06-13
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

## 3. Voice activity detection (VAD)

> **Status**: Exploring
> **Scope**: Detect speech vs silence to improve endpointing, trim silence, and
> provide natural commit points for streaming.

whisper.cpp ships a VAD model/config; OpenWhispr uses one (`whisperVad.json`).
Benefits: auto-stop on trailing silence (no need to release the key precisely),
skip empty/silent recordings, and give the streaming path real commit points
instead of fixed time intervals. Low-to-medium effort, broadly useful.

## 4. Cloud transcription option (BYOK)

> **Status**: Exploring
> **Scope**: Optional cloud transcription engine (OpenAI/Groq/etc.) as an
> alternative to local whisper.cpp, for speed or low-power devices.

We're local-only for transcription. Add a `cloud` engine behind the backend
abstraction (item 1) that POSTs audio to a transcription API with the user's
own key. Fits the same engine-selection UI; reuses the multi-provider plumbing
(item 5). Must remain opt-in (privacy).

## 5. Multi-provider management (BYOK)

> **Status**: Exploring
> **Scope**: Generalize hardcoded OpenAI/Anthropic into a managed list of AI
> providers usable across cleanup, translation, cloud transcription, and any
> future agent/actions.

OpenWhispr manages many providers (GPT-5, Claude, Gemini, Groq, local). Replace
our two-provider cleanup config with a provider registry (name, base URL, key,
model, type) that any AI feature can reference, surfaced in the config UI.
Foundation for items 2, 4, and a future actions/agent feature.

## 6. Meeting capture & diarization

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

## 7. Packaging: installers & auto-update

> **Status**: Exploring
> **Scope**: Native installers per OS and a built-in updater.

We're a Python daemon installed from source. OpenWhispr ships packaged
installers (electron-builder) and auto-update (`updater.js`). Candidates:
PyInstaller/Briefcase bundles or platform packages (.deb, .dmg, MSI/winget),
plus an update check against GitHub releases. Productization, not features, but
key for non-developer adoption.

## 8. Dictation intelligence & personalization (Wispr Flow gap analysis)

> **Status**: Exploring
> **Scope**: Make dictation itself smarter and more personal — real-time AI
> editing, voice editing commands, context/app awareness, mixed typing+voice,
> a learning dictionary, snippets, and whispered-speech support. Most of these
> build on the LLM provider work (items 2 & 5) and the streaming engine.

### 8.1 Real-time auto-edit while speaking

Apply AI cleanup **incrementally as you speak** (not a single post-pass), and
recognize **spoken self-corrections** inline ("no, make that Tuesday"). Builds
on the streaming path + a (local-or-cloud) LLM. Mode A (preview) is the natural
surface for showing live edits before injecting the final text.

### 8.2 Command mode

Voice commands that **edit already-written / selected text** — shorten,
lengthen, rephrase, change tone. Requires reading the current selection (or
recently injected text) and replacing it; LLM-driven. A distinct "command"
activation (separate hotkey or trigger word) vs normal dictation.

### 8.3 Context awareness

Detect the **focused application/window** and apply a matching formatting/tone
profile (email vs chat vs code). Needs platform-specific active-window
detection (X11 `_NET_ACTIVE_WINDOW`, Win32 `GetForegroundWindow`, macOS
Accessibility) plus user-defined per-app profiles in the config UI.

### 8.4 Multi-mode typing (voice + keyboard)

Let voice and typing mix: **read the surrounding text** in the target field and
continue mid-sentence / match its tone, rather than dumping a standalone block.
The hard part is reading the target field across arbitrary apps (accessibility
APIs / clipboard tricks) — a meaningful platform investment.

### 8.5 Personal dictionary with auto-learn

Extend the current (manual) vocabulary into a **learning dictionary**: when the
user corrects a transcription, learn new proper nouns and add them
automatically; manage entries (add / import / remove) in the config UI. Feeds
the existing `initial_prompt` biasing.

### 8.6 Snippets / voice text-replacement

**Trigger phrases that expand** to saved text or code (a voice text-expander),
applied as a post-transcription substitution step. Self-contained and
low-risk; manage snippets in the config UI.

### 8.7 Whispered speech support

Improve recognition of **whispered / very quiet speech** for dictating
discreetly in public. Likely a mix of input normalization/gain, model choice,
and tuning; research needed to gauge what whisper.cpp / alternative models can
do here.

---

## Other roadmap candidates (unscheduled)

- **Language-code normalization** (e.g. `zh -> zh-CN`) once non-Whisper engines
  are in play, to map between engine code sets.
- **Streaming window trimming**: true sliding-window decode with buffer trimming
  (currently the streaming engine decodes the whole buffer each tick). Pairs
  with VAD (item 3).
- **Simple "preferred language" mode** as an alternative to the configured
  list + cycle hotkey, for casual multilingual users.
- **Audio file upload / batch transcription** (transcribe existing recordings).
- **MCP server / public API** to expose dictation to AI assistants.
