# Human Test Plan: Full Settings Coverage

created: 2026-07-08T10:00:00+02:00
scope: All SamWhispers settings requiring physical interaction (speech, keyboard, visual confirmation)
prerequisite: SamWhispers running with `samwhispers-supervisor -v` and config UI at http://127.0.0.1:7891

## Instructions

Change **one setting at a time** via the config UI (or config.toml), save, wait for restart confirmation, then test. Verbose logging (`-v`) helps confirm prompt contents and server flags.

---

## Hotkey & Recording Mode

- [x] **hotkey.key** — Change to `ctrl+alt+r`. Press new combo → recording starts. Old combo (`ctrl+shift+space`) does nothing.
- [x] **hotkey.mode = hold** — Hold hotkey → recording indicator appears. Release → transcription fires.
- [x] **hotkey.mode = toggle** — Press once → recording starts. Press again → stops and transcribes.
- [ ] **hotkey.language_key** — Press `Ctrl+Shift+L`. Desktop notification shows language cycling (e.g. auto → en → fr → auto).

## Audio

- [x] **audio.max_duration = 5.0** — Record for 10+ seconds. Confirm recording auto-stops at ~5s mark.
- [ ] **audio.keep_stream_open = true** — Record twice in quick succession (<2s gap). Second recording starts instantly.
- [ ] **audio.keep_stream_open = false** — Same test. Notice perceptible delay (~200-500ms) on second start.

## Language & Accent

- [ ] **whisper.languages = ["en"]** — Speak a French sentence. Output is broken English (forced English transcription).
- [x] **whisper.languages = ["auto"]** — Speak French, then English in separate recordings. Both correctly detected.
- [ ] **whisper.languages = ["auto", "en", "fr"]** — Cycle through with language key. Notification shows each in order.
- [x] **whisper.accent = "fr"** — Speak English with French accent. Compare accuracy vs same sentence without accent bias (reset to "").
- [x] **whisper.accent + multilingual model** — Set accent to "fr", language to "fr". Confirm accent prompt is suppressed (check `-v` logs).

## Post-Processing

- [x] **postprocess.collapse_newlines = true** — Dictate 30+ seconds (triggers multi-segment). Output is single line with spaces.
- [ ] **postprocess.collapse_newlines = false** — Same long dictation. Output preserves `\n` between segments.
- [x] **postprocess.collapse_spaces = true** — After newline collapse, multiple adjacent spaces merge to one.
- [ ] **postprocess.collapse_spaces = false** — Multiple spaces preserved in output.
- [x] **postprocess.trim = true** — Output has no leading/trailing whitespace.
- [ ] **postprocess.trim = false** — Whitespace from whisper preserved at edges.
- [x] **postprocess.trailing = "none"** — Paste into Notepad. Cursor stays at end of text, same line.
- [x] **postprocess.trailing = "space"** — One trailing space after text.
- [x] **postprocess.trailing = "newline"** — Cursor on next line after paste.
- [x] **postprocess.trailing = "double_newline"** — One blank line separates text from cursor.
- [x] **postprocess.trailing = "tab"** — One tab character after text.

## Injection

- [ ] **inject.paste_delay = 0.0** — Transcribe into a slow app (Teams, Outlook). Check for partial or failed paste.
- [ ] **inject.paste_delay = 0.5** — Same app. Paste succeeds reliably but delay is clearly perceptible.
- [x] **inject.paste_delay = 0.1** (default) — Paste works in most apps without perceptible delay.

## Vocabulary Biasing

- [x] **vocabulary.words = ["RSSI", "pynput", "SamWhispers"]** — Say "RSSI" in a sentence. Confirm recognized correctly vs without vocabulary (where it often becomes "RSCI" or "RSS I").
- [x] **vocabulary per-lang: [vocabulary.en] words = ["Bluetooth"]** — Set language to "en", check `-v` log: "Bluetooth" appears in initial_prompt. Switch to "fr": absent from prompt.
- [x] **vocabulary in auto mode** — Only global `words` sent (per-lang ignored since language unknown). Check `-v` log.

## Filler Removal

- [ ] **filler.enabled = true** — Say "I went um to the uh store". Output: "I went to the store".
- [ ] **filler.enabled = false** — Same sentence. Output preserves "um" and "uh".
- [ ] **filler.words = ["hum"]** — Say "hum" as a filler pause. Confirm removed from output.
- [ ] **filler.use_builtins = true** — "um", "uh", "euh" all removed.
- [ ] **filler.use_builtins = false** — Only custom `words` removed. Built-in "um" preserved in output.
- [ ] **Elongated variants** — Say "euuuuuh" or "hmmmm". Confirm still matched and removed.

## AI Cleanup

- [ ] **cleanup.enabled = true, provider = "openai"** — Dictate "i went to store yesterday it was real good". Output has capitalization, grammar fixes.
- [ ] **cleanup.enabled = true, provider = "anthropic"** — Same test with Anthropic. Cleanup works.
- [ ] **cleanup.enabled = false** — Raw whisper output, no grammar/capitalization fixes.
- [ ] **Cleanup fallback** — Enable cleanup with invalid API key. Confirm original text is used (no crash, graceful fallback).

## Translation

- [ ] **translation.enabled = true, target_language = "fr"** — Dictate in English. Output is French translation.
- [ ] **translation.enabled = true, target_language = "de"** — Dictate in English. Output is German translation.
- [ ] **Translation fallback** — Enable with invalid key. Confirm original text used (no crash).
- [ ] **Translation + cleanup** — Both enabled. Confirm cleanup runs first, then translation.

## Overlay

- [ ] **overlay.enabled = true** — Start recording. Translucent pill appears near bottom-center with animated bars reacting to mic level. Release → spinner during transcription → disappears.
- [ ] **overlay.enabled = false** — Start recording. No on-screen indicator whatsoever.
- [ ] **Overlay in streaming preview mode** — Enable streaming + overlay. Evolving text shows in overlay.

## Streaming

- [ ] **streaming.enabled = true, engine = "chunked", output_mode = "preview"** — Hold hotkey, speak long sentence. Overlay shows evolving text. Release → final clean text pasted.
- [ ] **streaming.enabled = true, engine = "chunked", output_mode = "progressive"** — Hold hotkey, speak. Words appear in target app live as they lock in.
- [ ] **streaming.enabled = true, engine = "faster_whisper"** — Same test with faster_whisper. Confirm transcription works (check logs for CTranslate2 loading).
- [ ] **streaming.interval_seconds = 0.3** — Updates arrive noticeably faster than default (0.8).
- [ ] **streaming.interval_seconds = 2.0** — Updates arrive noticeably slower.
- [ ] **streaming.window_seconds = 5.0** — Speak for 30s+. CPU stays bounded (buffer trimming active).
- [ ] **streaming.min_words_after_sentence = 0 vs 3** — Compare trim aggressiveness. 0 = trims immediately after period. 3 = waits for 3 more words.
- [ ] **streaming.enabled = false** (default) — Single batch transcription on release. No live updates.

## Snippets

- [ ] **snippets.enabled = true, items: "my address" = "123 Main St, City 12345"** — Say "my address". Output is the expansion.
- [ ] **snippets.enabled = false** — Say "my address". Output is the literal words.
- [ ] **Longest-match priority** — Add "sig" = "Regards" and "signature" = "Best regards, John". Say "signature" → gets longer expansion.
- [ ] **Word boundary** — Add trigger "sig". Say "signal" → NOT expanded (word-boundary anchored).
- [ ] **snippets.bias_recognition = true** — Check `-v` log: trigger phrases appear in vocabulary prompt.
- [ ] **snippets.bias_recognition = false** — Check `-v` log: triggers absent from prompt.

## VAD (Voice Activity Detection)

### Client-side (toggle mode auto-stop)

- [ ] **vad.enabled = true, silence_duration = 2.0** — In toggle mode, press hotkey, speak, then go silent 3s. Recording auto-stops.
- [ ] **vad.silence_duration = 30.0** — Pause 5s. Recording continues (30s threshold not reached).
- [ ] **vad.silence_threshold = 0.5** — Even mild background noise triggers auto-stop quickly.
- [ ] **vad.silence_threshold = 0.001** — Only dead silence triggers auto-stop. Normal room ambient doesn't trigger.
- [ ] **VAD in hold mode** — Confirm client-side auto-stop does NOT fire (hold mode unaffected).

### Server-side (speech detection)

- [ ] **vad.threshold = 0.5** (default) — Normal speech recognized. Silence stripped from audio before decode.
- [ ] **vad.threshold = 0.9** — Only loud/clear speech passes. Quiet speech dropped.
- [ ] **vad.min_speech_duration_ms = 1000** — Speak a very short word (<500ms). Confirm it's dropped/missed.
- [ ] **vad.min_speech_duration_ms = 50** — Short words captured.
- [ ] **vad.speech_pad_ms = 0** — Beginning/end of words slightly clipped.
- [ ] **vad.speech_pad_ms = 200** — Full words captured with padding, no clipping.

---

## Execution Notes

- **Order**: Work top-to-bottom. Each section is self-contained — reset changed settings before moving to next section.
- **Baseline**: Before testing a section, confirm default behavior works, then modify the specific setting.
- **Evidence**: For subjective tests (accent quality, vocabulary accuracy), note "better/same/worse" rather than pass/fail.
- **Cleanup**: After testing, restore `config.toml` to working defaults.

## Completion Summary

| Section | Items | Passed | Failed | Skipped |
|---|---|---|---|---|
| Hotkey & Recording | 4 | | | |
| Audio | 3 | | | |
| Language & Accent | 5 | | | |
| Post-Processing | 11 | | | |
| Injection | 3 | | | |
| Vocabulary | 3 | | | |
| Filler Removal | 6 | | | |
| AI Cleanup | 4 | | | |
| Translation | 4 | | | |
| Overlay | 3 | | | |
| Streaming | 8 | | | |
| Snippets | 6 | | | |
| VAD Client-side | 5 | | | |
| VAD Server-side | 6 | | | |
| **Total** | **71** | | | |
