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

WSL cannot run the full daemon (no X11 for hotkeys/clipboard). Use native Windows instead. WSL is fine for development and running the test suite.

## Setting Up whisper-server

### Linux

```bash
# Install build tools if needed
sudo apt install cmake g++

git clone https://github.com/ggerganov/whisper.cpp.git
cd whisper.cpp
cmake -B build
cmake --build build --config Release -j$(nproc)

# Download a model
bash models/download-ggml-model.sh base.en

# Start the server
./build/bin/whisper-server -m models/ggml-base.en.bin --port 8080
```

### Windows

```powershell
# Requires Visual Studio 2022 with C++ workload, or MinGW, plus CMake
git clone https://github.com/ggerganov/whisper.cpp.git
cd whisper.cpp
cmake -B build
cmake --build build --config Release

# Download a model (PowerShell)
Invoke-WebRequest -Uri "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin" -OutFile "models/ggml-base.en.bin"

# Start the server
.\build\bin\Release\whisper-server.exe -m models\ggml-base.en.bin --port 8080
```

### Verify the Server

```bash
curl http://localhost:8080/
```

You should get an HTML response (200 OK). If port 8080 is already in use, pick another port (e.g., `--port 8090`) and update `whisper.server_url` in your config.

### Model Options

| Model | Size | Speed | Accuracy |
|---|---|---|---|
| `tiny.en` | ~75 MB | Fastest | Basic |
| `base.en` | ~150 MB | Fast | Good (recommended) |
| `small.en` | ~500 MB | Medium | Better |
| `medium.en` | ~1.5 GB | Slow | Best |

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

[whisper]
server_url = "http://localhost:8080"
language = "en"

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

[inject]
paste_delay = 0.1           # Seconds between clipboard write and Ctrl+V
```

## Usage

Start the daemon:

```bash
python -m samwhispers
```

With options:

```bash
python -m samwhispers -v                # Verbose/debug logging
python -m samwhispers -c myconfig.toml  # Custom config path
python -m samwhispers --version         # Show version
```

Once running, open any text editor or input field, hold the hotkey, speak, and release.

Stop with `Ctrl+C` or `SIGTERM`.

## AI Cleanup Setup

AI cleanup is optional and disabled by default. When enabled, transcribed text is sent to an AI model to fix grammar, punctuation, and capitalization before pasting.

1. Set `cleanup.enabled = true` in your config
2. Choose a provider (`openai` or `anthropic`)
3. Add your API key to the corresponding section

Typical cost: less than $0.01 per cleanup call with `gpt-4o-mini` or `claude-sonnet-4-20250514`.

If the cleanup API fails, the original transcription is used as fallback.

## Troubleshooting

### No audio / microphone not detected

- Check that your microphone is connected and recognized by the OS
- Linux: install `libportaudio2` (`sudo apt install libportaudio2`)
- Run `python -c "import sounddevice; print(sounddevice.query_devices())"` to list devices

### Whisper server not reachable

- Make sure `whisper-server` is running on the configured URL
- Test with: `curl http://localhost:8080/`
- If port 8080 is taken, use a different port and update `config.toml`
- Check firewall settings if using a non-localhost URL

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

### WSL

The full daemon does not work on WSL because there is no X11 display server for hotkeys and clipboard. Use native Windows for running the daemon. WSL works fine for development (`make check`, running tests, building whisper-server).

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
- WSL cannot run the full daemon (no X11)
- No per-application hotkey customization
- No streaming transcription (full recording is sent after release)
- Maximum recording duration is configurable but defaults to 5 minutes
- Clipboard is overwritten during text injection
- The simulated Ctrl+V may not work in all applications (e.g., some terminal emulators use Ctrl+Shift+V)

## License

MIT
