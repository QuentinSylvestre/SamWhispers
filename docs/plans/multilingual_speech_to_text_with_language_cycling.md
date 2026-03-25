# Multi-lingual Speech-to-Text with Language Cycling

> **Date**: 2026-07-15
> **Status**: Draft
> **Scope**: Auto-detect + per-hotkey language cycling across Linux, WSL, and Windows
> **Estimated effort**: 2-3 days

---

## 1) Goal

Add multi-language support to SamWhispers. Default to auto-detect so mixed-language sentences (code-switching) work naturally. Provide a configurable hotkey that cycles through a list of languages (e.g., `auto -> en -> fr -> auto`). Show the active language via log output and desktop notifications on all three platforms (Linux, WSL, Windows).

## 2) Current State

- `config.py:22-24` -- `WhisperConfig` has a single `language: str = "en"` field.
- `transcribe.py:17` -- `WhisperClient.__init__` takes `language` as a constructor arg, stored as `self._language`. Used in every `/inference` POST (`transcribe.py:37`).
- `app.py:42-44` -- `WhisperClient` is instantiated once at startup with a fixed language. No runtime language switching.
- `hotkeys.py` -- `HotkeyListener` (pynput, lines 87-148) and `WSLHotkeyListener` (PowerShell polling, lines 195-280) each handle a single hotkey combo. Both expose `start()`, `stop()`, `suppress()`, `resume()`.
- `hotkeys.py:207-237` -- WSL listener runs a single PowerShell subprocess with `GetAsyncKeyState` polling, emitting `PRESS`/`RELEASE` events.
- `cleanup.py:11-14` -- System prompt is English-only ("Fix grammar, punctuation, and capitalization"). Will be kept language-agnostic per design decision.
- `app.py:52-70` -- Platform branching: `is_wsl()` selects WSL backends, else pynput (works on both Linux and Windows natively).
- `config.example.toml:8` -- `language = "en"` is the only language config.
- No notification system exists.
- No language validation exists.

## 3) Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language selection UX | Hybrid: auto-detect by default, cycle hotkey to force a specific language | Auto-detect handles code-switching (mixed en/fr sentences); force mode helps when speaking purely one language on a small model |
| Default language list | `["auto"]` | Backward-compatible; single-language users see no change |
| Cleanup prompt | Language-agnostic (unchanged) | Modern LLMs infer language from context reliably |
| Language validation | Validate against whisper.cpp's 99 codes + `"auto"` at config load time | Fail fast on typos; the set is stable across whisper.cpp versions |
| WSL second hotkey | Extend existing PowerShell script to poll both key combos, emit `LANG` event | Single subprocess, minimal overhead vs. spawning a second process |
| Notification mechanism | `notify-send` (Linux), PowerShell `BurntToast`/balloon (WSL + Windows) | No new pip dependencies; graceful fallback if notification fails |
| Backward compatibility | Accept old `language = "en"` as `languages = ["en"]` | Existing configs keep working without changes |
| Config shape | `whisper.languages` (list) + `hotkey.language_key` (string) | Future-proofed for arbitrary language lists |

## 4) External Dependencies & Costs

### Required external changes

| Category | Change needed | Owner | Status |
|---|---|---|---|
| Whisper model | Users wanting multi-language must use multilingual models (`ggml-base.bin`, not `ggml-base.en.bin`) | User | Documented |

### Cost impact

None. No new cloud resources or API calls. Notifications use OS-native tools already present.

## 5) Implementation Phases

---

### Phase 1: Config -- languages list, language_key, validation

**Goal**: Replace `WhisperConfig.language: str` with `languages: list[str]`, add `HotkeyConfig.language_key`, validate language codes, handle backward compatibility.

**Files**: `src/samwhispers/config.py`, `config.example.toml`, `tests/test_config.py`

**Changes to `config.py`**:

Add the whisper.cpp language code set after the existing `_VALID_PROVIDERS` constant (~line 8):

```python
# ISO 639-1 codes supported by whisper.cpp, plus "auto" for auto-detection
WHISPER_LANGUAGES = {
    "auto",
    "en", "zh", "de", "es", "ru", "ko", "fr", "ja", "pt", "tr", "pl", "ca",
    "nl", "ar", "sv", "it", "id", "hi", "fi", "vi", "he", "uk", "el", "ms",
    "cs", "ro", "da", "hu", "ta", "no", "th", "ur", "hr", "bg", "lt", "la",
    "mi", "ml", "cy", "sk", "te", "fa", "lv", "bn", "sr", "az", "sl", "kn",
    "et", "mk", "br", "eu", "is", "hy", "ne", "mn", "bs", "kk", "sq", "sw",
    "gl", "mr", "pa", "si", "km", "sn", "yo", "so", "af", "oc", "ka", "be",
    "tg", "sd", "gu", "am", "yi", "lo", "uz", "fo", "ht", "ps", "tk", "nn",
    "mt", "sa", "lb", "my", "bo", "tl", "mg", "as", "tt", "haw", "ln", "ha",
    "ba", "jw", "su", "yue",
}
```

Update `WhisperConfig`:

```python
@dataclass
class WhisperConfig:
    server_url: str = "http://localhost:8080"
    languages: list[str] = field(default_factory=lambda: ["auto"])
```

Update `HotkeyConfig`:

```python
@dataclass
class HotkeyConfig:
    key: str = "ctrl+shift+space"
    mode: str = "hold"
    language_key: str = "ctrl+shift+l"
```

Update `_validate()` to check language codes:

```python
for lang in config.whisper.languages:
    if lang not in WHISPER_LANGUAGES:
        raise ValueError(
            f"Invalid language {lang!r}, must be one of: 'auto' or a whisper.cpp language code"
        )
if not config.whisper.languages:
    raise ValueError("whisper.languages must contain at least one entry")
```

Update `load_config()` for backward compatibility. Insert after `d = _merge(_to_dict(defaults), raw)` and before the `AppConfig(...)` construction:

```python
# Backward compat: whisper.language (str) -> whisper.languages (list)
whisper_raw = d.get("whisper", {})
if "language" in whisper_raw and "languages" not in whisper_raw:
    whisper_raw["languages"] = [whisper_raw.pop("language")]
elif "language" in whisper_raw and "languages" in whisper_raw:
    whisper_raw.pop("language")  # languages takes precedence
d["whisper"] = whisper_raw
```

<!-- resolves review finding #1 (backward compat placement) -->

Update `config.example.toml`:

```toml
[whisper]
server_url = "http://localhost:8080"
languages = ["auto"]  # Language cycle order. Use "auto" for auto-detection.
# For multi-language, use a multilingual model (e.g., ggml-base.bin, not ggml-base.en.bin).
# Recommended: ggml-medium.bin or larger for reliable auto-detection.
# Examples: ["auto", "en", "fr"], ["en"], ["auto"]

[hotkey]
key = "ctrl+shift+space"
mode = "hold"
language_key = "ctrl+shift+l"  # Cycles through whisper.languages list
```

**Exit criteria**:
- [ ] `pytest tests/test_config.py -v` passes with new tests for: `languages` list validation, invalid language code rejected, backward compat `language` -> `languages`, empty list rejected, `language_key` parsed
- [ ] Old config with `language = "en"` loads as `languages = ["en"]`

---

### Phase 2: WhisperClient runtime language switching

**Goal**: Allow `WhisperClient` to change language between requests.

**Files**: `src/samwhispers/transcribe.py`, `tests/test_transcribe.py`

Add a `language` property with setter to `WhisperClient`:

```python
@property
def language(self) -> str:
    return self._language

@language.setter
def language(self, value: str) -> None:
    self._language = value
```

Update `__init__` default to `"auto"`:

```python
def __init__(self, server_url: str, language: str = "auto") -> None:
```

**Exit criteria**:
- [ ] `pytest tests/test_transcribe.py -v` passes
- [ ] Language can be changed between calls and the new value is sent in the next request

---

### Phase 3: Notification module

**Goal**: Cross-platform desktop notifications (Linux, WSL, Windows) with graceful fallback.

**File**: `src/samwhispers/notify.py` (new), `tests/test_notify.py` (new)

```python
"""Cross-platform desktop notifications."""

from __future__ import annotations

import logging
import subprocess
import sys

log = logging.getLogger("samwhispers")


def notify(title: str, message: str) -> None:
    """Show a desktop notification. Logs warning on failure, never raises."""
    try:
        from samwhispers.wsl import is_wsl

        if is_wsl() or sys.platform == "win32":
            _notify_windows(title, message)
        else:
            _notify_linux(title, message)
    except Exception:
        log.warning("Desktop notification failed (title=%r)", title)


def _notify_linux(title: str, message: str) -> None:
    subprocess.run(
        ["notify-send", "--app-name=SamWhispers", title, message],
        check=True,
        timeout=5,
        capture_output=True,
    )


def _notify_windows(title: str, message: str) -> None:
    from samwhispers.wsl import is_wsl

    if is_wsl():
        from samwhispers.wsl import find_windows_exe

        ps = find_windows_exe("powershell.exe")
    else:
        ps = "powershell.exe"
    if not ps:
        log.warning("powershell.exe not found, cannot show notification")
        return
    # Escape single quotes for PowerShell
    t = title.replace("'", "''")
    m = message.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Information;"
        "$n.Visible = $true;"
        f"$n.ShowBalloonTip(3000, '{t}', '{m}', 'Info');"
        "Start-Sleep -Milliseconds 3100;"
        "$n.Dispose()"
    )
    subprocess.Popen(
        [ps, "-NoProfile", "-WindowStyle", "Hidden", "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
```

Key details:
- `notify()` is fire-and-forget, never raises.
- Windows/WSL: uses `NotifyIcon.ShowBalloonTip` -- no extra modules needed, works on all Windows versions.
- `Popen` (not `run`) so the 3s balloon display doesn't block the app.
- Linux: `notify-send` (libnotify).
- Add a `check_notify_available() -> bool` function that tests whether the notification backend works. Called during `_startup_checks()` in Phase 5.

<!-- resolves review finding #10 (notify-send startup check) -->

**Exit criteria**:
- [ ] `pytest tests/test_notify.py -v` passes (mock subprocess calls, verify correct platform dispatch)
- [ ] Notification failure doesn't crash the app

---

### Phase 4: Hotkey listeners -- language cycle callback

**Goal**: Add a second hotkey to both `HotkeyListener` and `WSLHotkeyListener` that fires an `on_language_cycle` callback.

**Files**: `src/samwhispers/hotkeys.py`, `tests/test_hotkeys.py`, `tests/test_wsl.py`

<!-- resolves review finding #5 (missing test_wsl.py) -->

**`HotkeyListener` changes** -- add `language_key_str` and `on_language_cycle` params:

```python
def __init__(
    self,
    hotkey_str: str,
    mode: str,
    on_start: Callable[[], None],
    on_stop: Callable[[], None],
    language_key_str: str | None = None,
    on_language_cycle: Callable[[], None] | None = None,
) -> None:
    # ... existing init ...
    self._lang_keys: set[Any] | None = None
    self._on_language_cycle = on_language_cycle
    if language_key_str and on_language_cycle:
        self._lang_keys = parse_hotkey(language_key_str)
```

Update `_on_press` to check the language hotkey (after the existing recording hotkey check):

```python
def _on_press(self, key: Any) -> None:
    with self._lock:
        if self._suppressed:
            return
        normalized = _normalize_key(key)
        if normalized in self._pressed:
            return
        self._pressed.add(normalized)

        hotkey_match = self._hotkey_keys.issubset(self._pressed)
        lang_match = (
            self._lang_keys is not None and self._lang_keys.issubset(self._pressed)
        )

    if lang_match and self._on_language_cycle:
        self._on_language_cycle()
    elif hotkey_match:
        if self._mode == "hold":
            self._on_start()
        else:
            with self._lock:
                if self._active:
                    self._active = False
                    self._on_stop()
                else:
                    self._active = True
                    self._on_start()
```

**`WSLHotkeyListener` changes** -- extend the PowerShell script to poll a second VK code array:

```python
def __init__(
    self,
    hotkey_str: str,
    mode: str,
    on_start: Callable[[], None],
    on_stop: Callable[[], None],
    language_key_str: str | None = None,
    on_language_cycle: Callable[[], None] | None = None,
) -> None:
    # ... existing init ...
    self._lang_vk_codes: list[int] | None = None
    self._on_language_cycle = on_language_cycle
    if language_key_str and on_language_cycle:
        self._lang_vk_codes = parse_hotkey_vk(language_key_str)
```

Update the PowerShell script in `start()` to add a second key array and `LANG` event:

```powershell
$langKeys = @(0xA2,0xA0,0x4C)  # example: ctrl+shift+l
$langDown = $false
# ... inside the while loop, after existing hotkey check:
$allLang = $true
foreach ($k in $langKeys) {
    if (([KS]::GetAsyncKeyState($k) -band 0x8000) -eq 0) {
        $allLang = $false; break
    }
}
if ($allLang -and -not $langDown) { $langDown = $true; Write-Output "LANG"; [Console]::Out.Flush() }
elseif (-not $allLang -and $langDown) { $langDown = $false }
```

Update `_read_loop` to handle `LANG`:

```python
if line == "LANG":
    if self._on_language_cycle:
        self._on_language_cycle()
    continue
```

**Exit criteria**:
- [ ] `pytest tests/test_hotkeys.py tests/test_wsl.py -v` passes with new tests for language key parsing (both pynput and VK)
- [ ] Existing hotkey tests still pass (backward compat: `language_key_str=None` is the default)

---

### Phase 5: App orchestration -- wire language cycling and notifications

**Goal**: Add language state to `SamWhispers`, wire the cycle callback, fire notifications.

**Files**: `src/samwhispers/app.py`, `tests/test_app.py`

Add language state and cycle method to `SamWhispers.__init__`:

```python
self._languages = config.whisper.languages
self._lang_index = 0

self.whisper = WhisperClient(
    server_url=config.whisper.server_url,
    language=self._languages[0],
)
```

Add the cycle callback:

```python
def _cycle_language(self) -> None:
    with self._lock:
        if self._state != State.IDLE:
            log.debug("Busy (%s), ignoring language cycle", self._state.value)
            return
    self._lang_index = (self._lang_index + 1) % len(self._languages)
    lang = self._languages[self._lang_index]
    self.whisper.language = lang
    label = "Auto-detect" if lang == "auto" else lang
    log.info("Language switched to: %s", label)
    from samwhispers.notify import notify
    notify("SamWhispers", f"Language: {label}")
```

<!-- resolves review findings #11 (ignore cycle during recording), #13 (auto label) -->

Update hotkey listener construction (both WSL and native paths) to pass the new params:

```python
# In the is_wsl() branch:
self.hotkey_listener = WSLHotkeyListener(
    hotkey_str=config.hotkey.key,
    mode=config.hotkey.mode,
    on_start=self._on_record_start,
    on_stop=self._on_record_stop,
    language_key_str=config.hotkey.language_key if len(self._languages) > 1 else None,
    on_language_cycle=self._cycle_language if len(self._languages) > 1 else None,
)
# Same pattern for the native HotkeyListener branch
```

Only wire the language hotkey when there are multiple languages configured. Single-language users see no change.

Add a startup log/notification showing the initial language:

```python
# In _startup_checks(), after existing checks:
lang = self._languages[0]
label = "Auto-detect" if lang == "auto" else lang
if len(self._languages) > 1:
    log.info("Language: %s (cycle with '%s' through %s)",
             label, self.config.hotkey.language_key, self._languages)
    from samwhispers.notify import notify
    notify("SamWhispers", f"Language: {label}")
else:
    log.info("Language: %s", label)
```

<!-- resolves review finding #12 (startup log for single-language) -->

**Exit criteria**:
- [ ] `pytest tests/test_app.py -v` passes with new tests for: language cycling changes whisper language, cycling wraps around, single-language config doesn't wire language hotkey
- [ ] `pytest tests/ -v` -- full suite passes
- [ ] `make check` passes

---

### Phase 6: Documentation updates

**Goal**: Update README and example config for multi-language support.

**Files**: `README.md`, `config.example.toml`

README changes:
- Update the model table to include multilingual recommendations and a note that `.en` models don't support multi-language
- Add a "Multi-language" section explaining: config, language cycling hotkey, auto-detect behavior, model recommendations
- Update the config options section with `languages` and `language_key`
- Add troubleshooting entry for wrong language detection (recommend larger model)
- Note in Known Limitations: auto-detect quality depends on model size, `base` model may struggle with code-switching

`config.example.toml` is already updated in Phase 1.

**Exit criteria**:
- [ ] README documents all new config options
- [ ] Model table includes multilingual guidance
- [ ] Troubleshooting covers language detection issues

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Auto-detect poor on `base` model for short clips | Wrong language transcription | Log warning at startup recommending `medium`+ when `auto` or multiple languages configured |
| `.en` model files ignore language parameter | Multi-language silently broken | Document in README that multilingual models are required |
| `notify-send` not installed on Linux | No desktop notification | Graceful fallback: log warning, don't crash |
| PowerShell `NotifyIcon` blocked by policy | No notification on Windows/WSL | Fire-and-forget with `Popen`, failure logged |
| Language hotkey conflicts with recording hotkey | Both fire simultaneously | Check language hotkey first in `_on_press`; if language combo is a subset of recording combo, language takes priority |
| Old configs with `language = "en"` break | App won't start | Backward compat: auto-convert `language` to `languages = [language]` |
| Extended PowerShell script has bugs | WSL hotkeys break | Test both event types in `test_wsl.py` |

## 7) Verification

### Automated
```bash
make check  # lint + typecheck + tests
```

### Manual (after Phase 5)
1. Set `languages = ["auto", "en", "fr"]` in config
2. Start SamWhispers: `python -m samwhispers -v`
3. Verify startup notification shows "Language: auto"
4. Press `Ctrl+Shift+L` -- verify notification shows "Language: en"
5. Press again -- verify "Language: fr"
6. Press again -- verify "Language: auto" (wraps)
7. Record in each mode, verify transcription uses the correct language parameter

### Backward compat check
1. Use old config with `language = "en"` (no `languages` key)
2. Verify app starts normally with `languages = ["en"]`
3. Verify no language cycle hotkey is active (single language)

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `config.example.toml` | New `languages`, `language_key` fields | Phase 1 |
| `README.md` | Multi-language section, model table, troubleshooting | Phase 6 |

## 9) Autonomous Development Protocol

### Operating rules

1. Implement each phase fully before moving to the next.
2. Run `make check` after every phase. Fix failures before committing. Loop up to 5 times.
3. Make reasonable implementation decisions without asking. Document non-obvious choices in code comments.
4. Only stop and ask the user when a design decision would change the agreed spec or a blocker requires user action.
5. Commit after each phase using Conventional Commits.
6. Notifications are platform-dependent -- mock subprocess calls in tests, never call real `notify-send` or `powershell.exe`.

### Self-verification checklist (run after each phase)

```bash
python -m pytest tests/ -v
mypy src/
ruff check src/ tests/
ruff format --check src/ tests/
```

## 10) Implementation Divergences from Plan

<Reserved -- filled during implementation>

## 11) Review Log

### 2026-07-15 -- Self-review Cycle 1 (sub-agent unavailable) -- personas: Implementability Reviewer, Reliability Engineer, End-User Advocate

7 findings (0 High, 4 Medium, 3 Low). 6 auto-resolved.

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | Backward compat code placement in `load_config()` unclear -- must run after `_merge()` and before `AppConfig()` construction | Medium | Resolved -- added explicit placement instructions |
| 5 | `tests/test_wsl.py` not listed in Phase 4 despite WSL listener changes | Medium | Resolved -- added to Phase 4 files and exit criteria |
| 10 | No startup check for `notify-send` availability on Linux | Medium | Resolved -- added `check_notify_available()` to Phase 3, called in Phase 5 startup checks |
| 11 | Language cycle during recording could change language for in-flight transcription | Medium | Resolved -- `_cycle_language()` now checks state and ignores if not IDLE |
| 7 | `WhisperClient._language` written from hotkey thread, read from worker thread (CPython string assignment is atomic via GIL, acceptable) | Low | Noted -- documented as acceptable behavior |
| 12 | Startup log mentions language_key even for single-language configs | Low | Resolved -- conditional log message |
| 13 | "Language: auto" not user-friendly | Low | Resolved -- display "Auto-detect" for auto |
