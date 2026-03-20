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
- [whisper.cpp](https://github.com/ggerganov/whisper.cpp) built with `whisper-server`
- A Whisper model file (e.g., `ggml-base.en.bin`)
- Linux: `libportaudio2`, `xclip`, X11 display server
- Windows: no extra system dependencies

### Linux System Packages

```bash
sudo apt install libportaudio2 xclip
```

## Setting Up whisper-server

### Build whisper.cpp

```bash
git clone https://github.com/ggerganov/whisper.cpp.git
cd whisper.cpp
cmake -B build
cmake --build build --config Release
```

### Download a Model

```bash
# Base English model (~150 MB, good balance of speed and accuracy)
./models/download-ggml-model.sh base.en
```

Other model options: `tiny.en` (fastest), `small.en` (better accuracy), `medium.en` (best accuracy, slower).

### Start the Server

```bash
./build/bin/whisper-server -m models/ggml-base.en.bin --port 8080
```

The server is ready when you see it listening on the configured port. Verify with:

```bash
curl http://localhost:8080/
```

## Install

```bash
git clone <repo-url>
cd SamWhispers
python3 -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows
pip install -e ".[dev]"
```

Or use the Makefile:

```bash
make setup
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
python -m samwhispers -v              # Verbose/debug logging
python -m samwhispers -c myconfig.toml  # Custom config path
python -m samwhispers --version       # Show version
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
- Check firewall settings if using a non-localhost URL

### Text not pasting / clipboard errors

- Linux: install `xclip` (`sudo apt install xclip`)
- Make sure you have a running X11 display server
- Increase `inject.paste_delay` if text appears partially

### Hotkey not working

- Linux: pynput requires X11. Wayland is not supported in v1
- Some Linux setups require the user to be in the `input` group:
  ```bash
  sudo usermod -aG input $USER
  ```
  Then log out and back in
- Check that the hotkey combination isn't already captured by another application

### Wayland

Wayland is not supported. SamWhispers requires X11 for global hotkeys via pynput. On GNOME, you can switch to X11 at the login screen.

## Development

```bash
make setup      # Create venv and install dependencies
make check      # Run lint + typecheck + tests
make test       # Run tests only
make lint       # Run ruff linter and formatter check
make typecheck  # Run mypy
make format     # Auto-format code
make clean      # Remove venv and caches
```

## Known Limitations

- Wayland is not supported (X11 only on Linux)
- No per-application hotkey customization
- No streaming transcription (full recording is sent after release)
- Maximum recording duration is configurable but defaults to 5 minutes
- Clipboard is overwritten during text injection
- The simulated Ctrl+V may not work in all applications (e.g., some terminal emulators use Ctrl+Shift+V)

## License

MIT
