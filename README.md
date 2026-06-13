# SamWhispers

Local voice-to-text daemon. Press a hotkey, speak, release -- your words appear as text in the active application.

Uses [whisper.cpp](https://github.com/ggerganov/whisper.cpp) for local transcription. No cloud dependency for core functionality. Optional AI cleanup via OpenAI or Anthropic APIs.

## How It Works

1. Hold a global hotkey (default: `Ctrl+Shift+Space`)
2. Speak into your microphone
3. Release the hotkey
4. Audio is transcribed locally via whisper-server
5. Text is (optionally) cleaned up by an AI model
6. Result is pasted into the active application via clipboard

## Quick start

```bash
# 1. install (from the cloned repo)
make setup                         # or: pip install -e .
source .venv/bin/activate          # if you used make setup

# 2. one-command setup: builds/downloads whisper-server, fetches a model,
#    and writes a starter config
samwhispers-setup                  # add --model small for multilingual

# 3. run it (tray icon + web UI at http://127.0.0.1:7891)
samwhispers-supervisor

# 4. (optional) start automatically at login
samwhispers-autostart enable
```

`samwhispers-setup` builds whisper.cpp from source from the official repo on all
platforms (needs `git`, `cmake`, and a C++ compiler — on Windows, Visual Studio
Build Tools with the C++ workload, or MinGW). Everything lands in your user data
dir. The manual steps below are the fallback / advanced path.

## Prerequisites

- Python 3.11+
- [whisper.cpp](https://github.com/ggerganov/whisper.cpp) built with `whisper-server` (see below)
- A Whisper model file (e.g., `ggml-base.en.bin`)

### Linux (X11)

```bash
sudo apt install libportaudio2 xclip
```

Wayland is not supported in v1 -- pynput requires X11 for global hotkeys.

### Windows

No extra system dependencies. PortAudio is bundled with the `sounddevice` pip package, and clipboard/hotkeys work natively.

### WSL

WSL2 is fully supported via Windows interop (`clip.exe`, `powershell.exe`). No X11 needed. Windows interop must be enabled (default).

## Setting Up whisper-server

SamWhispers automatically starts and manages whisper-server for you. You just need to build it and download a model.

### Linux

```bash
# Install build tools if needed
sudo apt install cmake g++

# Clone into the tools/ directory (already gitignored)
git clone https://github.com/ggerganov/whisper.cpp.git tools/whisper.cpp
cd tools/whisper.cpp
cmake -B build
cmake --build build --config Release -j$(nproc)

# Download a model
bash models/download-ggml-model.sh base.en
cd ../..
```

### Windows

```powershell
# Requires Visual Studio 2022 with C++ workload, or MinGW, plus CMake
git clone https://github.com/ggerganov/whisper.cpp.git tools/whisper.cpp
cd tools/whisper.cpp
cmake -B build
cmake --build build --config Release

# Download a model (PowerShell)
Invoke-WebRequest -Uri "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin" -OutFile "models/ggml-base.en.bin"
cd ../..
```

### WSL

When running SamWhispers under WSL, you must build whisper.cpp inside WSL (producing a Linux binary). Do not use a Windows `.exe` build -- it will not work as a managed subprocess from WSL.

```bash
# Build inside WSL (same as Linux instructions above)
git clone https://github.com/ggerganov/whisper.cpp.git tools/whisper.cpp
cd tools/whisper.cpp
cmake -B build
cmake --build build --config Release -j$(nproc)
bash models/download-ggml-model.sh base.en
cd ../..
```

### Using an External Server (Unmanaged Mode)

If you prefer to run whisper-server yourself (custom flags, remote server, etc.), disable managed mode:

```toml
[whisper]
managed = false
server_url = "http://localhost:8080"
```

In unmanaged mode, SamWhispers connects to the server at `server_url` but does not start or stop it. You are responsible for running whisper-server separately.

### Verify the Server

```bash
curl http://localhost:8080/
```

You should get an HTML response (200 OK). If port 8080 is already in use, pick another port (e.g., `--port 8090`) and update `whisper.server_url` in your config.

### Model Options

| Model | Size | Speed | Accuracy | Languages |
|---|---|---|---|---|
| `tiny.en` | ~75 MB | Fastest | Basic | English only |
| `base.en` | ~150 MB | Fast | Good | English only |
| `tiny` | ~75 MB | Fastest | Basic | Multilingual |
| `base` | ~150 MB | Fast | Good (recommended for English) | Multilingual |
| `small` | ~500 MB | Medium | Better | Multilingual |
| `medium` | ~1.5 GB | Slow | Best | Multilingual (recommended for multi-language) |

The `.en` models are English-only and faster. The multilingual models (without `.en`) support auto-detection and 99 languages. For multi-language use or auto-detection, use `medium` or larger for reliable results.

## Install SamWhispers

### Linux / macOS

```bash
git clone <repo-url>
cd SamWhispers
make setup    # creates .venv, installs everything
```

If `make` is not available:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Windows

```powershell
git clone <repo-url>
cd SamWhispers
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

## Configuration

Copy the example config and edit as needed:

```bash
cp config.example.toml config.toml
```

SamWhispers searches for config in this order:
1. `./config.toml` (current directory)
2. `~/.config/samwhispers/config.toml`

If no config file is found, defaults are used.

### Config Options

```toml
[hotkey]
key = "ctrl+shift+space"   # Hotkey combination
mode = "hold"               # "hold" (release to stop) or "toggle" (press to start/stop)
language_key = "ctrl+shift+l"  # Cycles through configured languages

[whisper]
server_url = "http://localhost:8080"
languages = ["auto"]        # Language cycle order; "auto" for auto-detection
managed = true              # false to run your own whisper-server
server_bin = "tools/whisper.cpp/build/bin/whisper-server"
model_path = "tools/whisper.cpp/models/ggml-base.en.bin"
# Examples: ["auto", "en", "fr"], ["en"], ["auto"]
accent = ""                 # Native language code for accent biasing (see Accent Bias section)
# accent_prompt = ""        # Custom accent prompt override (requires accent to be set)

[audio]
sample_rate = 16000         # Must be 16000 for whisper.cpp
max_duration = 300.0        # Max recording length in seconds

[cleanup]
enabled = false             # Enable AI text cleanup
provider = "openai"         # "openai" or "anthropic"

[cleanup.openai]
api_key = ""                # Your OpenAI API key
model = "gpt-4o-mini"
api_base = "https://api.openai.com/v1"

[cleanup.anthropic]
api_key = ""                # Your Anthropic API key
model = "claude-sonnet-4-20250514"
api_base = "https://api.anthropic.com"

[postprocess]
collapse_newlines = true    # Replace \n from whisper segments with spaces
collapse_spaces = true      # Collapse multiple spaces into one
trim = true                 # Strip leading/trailing whitespace
trailing = "newline"        # Append after text: "none", "space", "newline", "double_newline", "tab"

[inject]
paste_delay = 0.1           # Seconds between clipboard write and Ctrl+V

[vocabulary]
words = []                  # Words to bias Whisper toward recognizing
# Per-language: [vocabulary.en] words = ["Bluetooth Low Energy"]

[filler]
enabled = true              # Remove filler words from transcription
use_builtins = true         # Include built-in filler lists (English + French)
words = []                  # Additional filler words to remove
```

## Usage

Launcher scripts in the project root activate the venv for you:

```bash
# Linux / macOS
./samwhispers.sh

# WSL
./samwhispers-wsl.sh

# Windows
samwhispers.bat
```

All arguments are forwarded:

```bash
./samwhispers.sh -v                # Verbose/debug logging
./samwhispers.sh -c myconfig.toml  # Custom config path
./samwhispers.sh --version         # Show version
```

You can also run manually with an activated venv:

```bash
samwhispers              # start in the background (frees the terminal)
samwhispers -f           # or run in the foreground (attached, shows logs)
```

`samwhispers` (also `samwhispers-supervisor` / `python -m samwhispers`) launches
the full app — tray, web UI, and a managed worker — configured by your
`config.toml`. By **default it detaches to the background** so the terminal is
free and closing it won't stop SamWhispers; pass `--foreground` / `-f` to keep it
attached (useful for seeing logs). (`samwhispers worker` runs just the worker and
is used internally — you don't run it directly.)

Only **one instance runs at a time** — launching `samwhispers` again won't start a
second copy; it just opens the running instance's config UI.

Once running, open any text editor or input field, hold the hotkey, speak, and release.

Quit from the tray's **Quit** item (or `Ctrl+C` if you ran it with `-f`).

### On-screen indicator

While recording, a small translucent pill appears near the bottom-center of the
screen with a few white bars that react to your microphone level; it turns into
a spinner while waiting for transcription, then disappears. Toggle it with
`overlay.enabled` (or from the config UI). It needs a graphical display and is
silently skipped without one (e.g. over SSH). On Linux it targets X11, like the
rest of the app.

### Run in the background with a tray icon

To run SamWhispers in the background with a system tray icon (and start it
automatically on login), use the supervisor instead of running the worker
directly:

```bash
samwhispers-supervisor            # tray icon + managed worker
samwhispers-supervisor --no-tray  # headless (no display)
```

The tray icon shows status (running / paused / stopped) and offers Open
settings, Pause/Resume, Restart, and Quit. See [docs/STARTUP.md](docs/STARTUP.md)
for start-on-login setup (systemd user service on Linux, launchd on macOS, Task
Scheduler on Windows).

### Config UI (browser)

When the supervisor runs, it serves a local config UI at
**http://127.0.0.1:7891/** (also reachable via the tray's *Open settings*).
Edit any setting in the browser and click **Save** — the config is validated,
written to `config.toml`, and the worker restarts automatically when a change
requires it. The UI also shows worker status and offers Pause/Resume/Restart.
The supervisor owns the managed `whisper-server`, so most changes restart only
the lightweight worker; the whisper model is reloaded only when `[whisper]`
settings change.

```bash
samwhispers-supervisor --web-port 9000  # use a different port
samwhispers-supervisor --no-web         # disable the UI
```

The Whisper section lets you pick a model from the ones detected on disk (or
enter a custom path), and **download** any standard whisper.cpp model on demand
with a progress indicator — handy for grabbing a model you don't have yet.

The server binds to loopback only and has no authentication, so don't expose
the port beyond `127.0.0.1`.

### Transcription history

When `history.enabled` is set (default), each transcription is saved to a local
SQLite database (`<data-dir>/samwhispers/history.db`). Browse, search, copy, and
delete entries from the **History** tab in the config UI. Set
`history.max_entries` to cap retention (`0` = unlimited); the oldest entries are
pruned automatically. History never leaves your machine.

### Translation

Enable `translation.enabled` and pick a `translation.target_language` to have
dictated text translated before it's injected. Translation uses the AI provider
and API keys configured in `[cleanup]` (so set those up first). The History tab
shows both the original transcription and its translation. As with cleanup, if
the API call fails the original text is used.

### Streaming (continuous) transcription

By default SamWhispers transcribes once, when you release the hotkey. Enable
`streaming.enabled` for continuous transcription that updates while you speak.
All streaming settings are editable in the config UI.

- **Engine** (`streaming.engine`):
  - `chunked` — re-decodes the audio via the existing whisper.cpp server every
    `interval_seconds`. No extra dependency; reuses your current setup.
  - `faster_whisper` — uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
    (CTranslate2) for incremental decoding. Install with
    `pip install samwhispers[faster-whisper]` and set `streaming.model`.
- **Output mode** (`streaming.output_mode`):
  - `preview` (A) — the evolving text shows in the on-screen overlay; the target
    app receives only the final paragraph (with cleanup/translate applied). Best
    when you want the model to revise earlier words before committing.
  - `progressive` (B) — stable words are typed into the target app as they lock
    in. Cleanup/translation don't apply in this mode (text is already committed).

Streaming re-decodes repeatedly, so it uses more CPU than batch mode; smaller
models keep up best. Batch mode remains the default.

## AI Cleanup Setup

AI cleanup is optional and disabled by default. When enabled, transcribed text is sent to an AI model to fix grammar, punctuation, and capitalization before pasting.

1. Set `cleanup.enabled = true` in your config
2. Choose a provider (`openai` or `anthropic`)
3. Add your API key to the corresponding section

Typical cost: less than $0.01 per cleanup call with `gpt-4o-mini` or `claude-sonnet-4-20250514`.

If the cleanup API fails, the original transcription is used as fallback.

## Custom Vocabulary

SamWhispers can bias Whisper toward recognizing specific words by sending them as an `initial_prompt`. This is useful for proper nouns, project names, and technical terms that Whisper frequently misrecognizes.

### Setup

Add words to the `[vocabulary]` section in `config.toml`:

```toml
[vocabulary]
words = ["RSSI", "pynput", "SamWhispers", "BLE"]
```

### Per-language Vocabulary

You can define language-specific words that are only sent when that language is active. Per-language words are merged with the global list:

```toml
[vocabulary]
words = ["RSSI", "BLE"]  # Always sent

[vocabulary.en]
words = ["Bluetooth Low Energy"]

[vocabulary.fr]
words = ["Bluetooth basse consommation"]
```

When the active language is `en`, Whisper receives: `RSSI, BLE, Bluetooth Low Energy`. When set to `fr`: `RSSI, BLE, Bluetooth basse consommation`.

**Note**: In `auto` mode, only the global words are sent because the language is unknown until after transcription. If you primarily use auto-detect, put your most important terms in the global `words` list rather than per-language sections.

### Tips

- Keep the list short. The `initial_prompt` token limit is roughly 150-200 words total (global + per-language combined). A warning is logged if you exceed 100 words.
- Use broadly applicable terms. Domain-specific jargon for a single conversation may cause mild misrecognition in unrelated contexts.
- Duplicates are automatically removed (case-insensitive).

## Accent Bias

If you speak with a non-native accent (e.g., French-accented English), you can
bias Whisper's decoder to improve recognition accuracy:

```toml
[whisper]
accent = "fr"    # Your native language code
```

Use any language code supported by whisper.cpp (e.g., `fr`, `de`, `ja`, `hi`, `ta` --
99 languages total). See `config.py` for the full `WHISPER_LANGUAGES` set, or run
with `-v` to see the prompt in startup logs.

This adds a conditioning prompt to Whisper's decoder. When you cycle to a
language that matches your accent (e.g., switching to French), the accent
prompt is automatically suppressed since it's not needed.

For custom control, override the generated prompt:

```toml
[whisper]
accent = "fr"
accent_prompt = "The speaker is a native French speaker with a strong accent."
```

Note: Accent biasing conditions the text decoder, not the acoustic model.
It helps with ambiguous words but cannot fix purely acoustic misrecognitions.
For best results, combine with a larger model (medium or large).

The accent prompt is combined with your vocabulary list into a single prompt.
If you use both features, keep the total short to stay within Whisper's ~224
token limit.

When using auto-detect (`languages = ["auto"]`), the accent prompt is always
active because the detected language is not known at prompt time. For best
results with accent biasing, use explicit language codes.

## Filler Word Removal

SamWhispers automatically removes filler words (um, uh, euh, etc.) from transcriptions. This runs as a post-processing step before any AI cleanup, so it works without cloud dependencies.

### Built-in Fillers

Two languages are covered out of the box:

- **English**: um, uh, hmm, mm, mhm, mmm, ah, oh, er
- **French**: euh, bah, beh, ben, hein, mmh, mh, pfff

Only unambiguous interjections are included. Borderline fillers like "like" or "genre" are excluded because they are also real words.

### Elongated Variants

Filler removal automatically catches elongated variants. For example, `euh` also matches `euuuuuh`, and `hmm` matches `hmmmmmm`. You do not need to list every possible spelling.

Note: very short real words that look like elongated fillers may occasionally be caught. For example, `er` (a built-in filler) also matches `err` as in "to err is human." If this is a problem for your use case, disable builtins and define your own filler list.

### Custom Filler Words

Add your own filler words in `config.toml`:

```toml
[filler]
words = ["hum", "bof", "ouais"]
```

Custom words are added alongside the built-in lists. To use only your own words without the built-in lists:

```toml
[filler]
use_builtins = false
words = ["hum", "bof"]
```

### Disabling Filler Removal

```toml
[filler]
enabled = false
```

### How It Works

Filler removal uses word-boundary-anchored regex matching. This means `ben` is removed as a filler but `benefit` is preserved. Orphaned punctuation is cleaned up automatically -- `"I went to the, euh, store"` becomes `"I went to the store"`.

## Multi-language Support

SamWhispers supports 99 languages via whisper.cpp. By default, it uses auto-detection (`languages = ["auto"]`).

### Setup

1. Use a multilingual model (e.g., `ggml-medium.bin`, not `ggml-medium.en.bin`)
2. Configure your languages in `config.toml`:

```toml
[whisper]
languages = ["auto", "en", "fr"]  # Cycle order

[hotkey]
language_key = "ctrl+shift+l"     # Hotkey to cycle languages
```

3. Press the language key to cycle: Auto-detect -> English -> French -> Auto-detect -> ...

A desktop notification shows the active language on each switch.

### Tips

- Auto-detect works best with `medium` or larger models
- When speaking purely in one language, force it for better accuracy on smaller models
- Mixed-language sentences (code-switching) work best in auto-detect mode
- The `.en` model variants are English-only and will ignore the language setting

## Troubleshooting

### No audio / microphone not detected

- Check that your microphone is connected and recognized by the OS
- Linux: install `libportaudio2` (`sudo apt install libportaudio2`)
- Run `python -c "import sounddevice; print(sounddevice.query_devices())"` to list devices

### Whisper server not reachable

- In managed mode (default), SamWhispers starts whisper-server automatically. If it fails, check:
  - The binary exists at the configured `whisper.server_bin` path
  - The model exists at the configured `whisper.model_path` path
  - The binary is executable (`chmod +x` on Linux)
  - The port is not already in use
- In unmanaged mode (`whisper.managed = false`), make sure `whisper-server` is running on the configured URL
- Test with: `curl http://localhost:8080/`
- If port 8080 is taken, use a different port and update `config.toml`

### Text not pasting / clipboard errors

- Linux: install `xclip` (`sudo apt install xclip`)
- Make sure you have a running X11 display server
- Increase `inject.paste_delay` if text appears partially
- Windows: should work out of the box

### Hotkey not working

- Linux: pynput requires X11. Wayland is not supported in v1
- Some Linux setups require the user to be in the `input` group:
  ```bash
  sudo usermod -aG input $USER
  ```
  Then log out and back in
- Check that the hotkey combination isn't already captured by another application
- Windows: run as administrator if hotkeys are not detected

### Wrong language detected

- Use a larger model (`medium` or `large`) for reliable auto-detection
- Make sure you're using a multilingual model (e.g., `ggml-medium.bin`, not `ggml-medium.en.bin`)
- For short phrases, force the language instead of using auto-detect
- Press the language cycle hotkey (default: `Ctrl+Shift+L`) to switch to a specific language

### WSL

SamWhispers has native WSL support. It automatically detects WSL and uses Windows interop:

- **Clipboard**: `clip.exe` (write) and `powershell.exe Get-Clipboard` (read)
- **Paste simulation**: PowerShell `SendKeys('^v')`
- **Hotkey detection**: PowerShell `GetAsyncKeyState` polling (15ms interval)

Requirements:
- Windows interop must be enabled (default in WSL2)
- `clip.exe` and `powershell.exe` must be accessible (usually via `/mnt/c/Windows/System32/`)

If hotkeys or clipboard don't work, verify interop:
```bash
echo "test" | /mnt/c/Windows/System32/clip.exe
/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -c "Get-Clipboard"
```

Known WSL limitations:
- Hotkey detection uses polling (~15ms latency) instead of native hooks
- The active window for paste must be a Windows application (not a WSL terminal)
- `SendKeys` may not work with all applications

### Wayland

Wayland is not supported. SamWhispers requires X11 for global hotkeys via pynput. On GNOME, you can switch to X11 at the login screen.

## Development

### Linux / macOS

```bash
make setup      # Create venv and install dependencies
make check      # Run lint + typecheck + tests
make test       # Run tests only
make lint       # Run ruff linter and formatter check
make typecheck  # Run mypy
make format     # Auto-format code
make clean      # Remove venv and caches
```

### Windows (no make)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

# Run checks manually
python -m ruff check src/ tests/
python -m ruff format --check src/ tests/
python -m mypy src/
python -m pytest tests/ -v
```

### Build dependencies on Linux

Building `pynput`'s `evdev` dependency from source requires:

```bash
sudo apt install gcc python3-dev linux-libc-dev
```

These are not needed on Windows.

## Known Limitations

- Wayland is not supported (X11 only on Linux)
- WSL hotkey detection uses polling (~15ms latency) instead of native hooks
- No per-application hotkey customization
- No streaming transcription (full recording is sent after release)
- Maximum recording duration is configurable but defaults to 5 minutes
- Clipboard is overwritten during text injection
- The simulated Ctrl+V may not work in all applications (e.g., some terminal emulators use Ctrl+Shift+V)
- Auto-detect language quality depends on model size; `base` may struggle with short clips or code-switching
- Desktop notifications require `notify-send` on Linux (install via `sudo apt install libnotify-bin`)

## License

MIT
