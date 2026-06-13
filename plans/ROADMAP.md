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

## Other roadmap candidates (unscheduled)

- **Language-code normalization** (e.g. `zh -> zh-CN`) once non-Whisper engines
  are in play, to map between engine code sets.
- **Streaming window trimming**: true sliding-window decode with buffer trimming
  (currently the streaming engine decodes the whole buffer each tick).
- **VAD-based commit points** for streaming (commit on detected silences).
- **Simple "preferred language" mode** as an alternative to the configured
  list + cycle hotkey, for casual multilingual users.
