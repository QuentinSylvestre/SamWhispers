# Session Test Plan

Covers everything implemented in this session (supervisor + tray, config web
UI, history, translation, warm whisper-server, overlay, streaming, model
selection/download, plus polish). Use it for targeted manual testing — most of
these need a real display / microphone / whisper model that CI can't exercise.

> Legend: **[P1]** core path · **[P2]** secondary · _needs:_ display / audio /
> model / 2nd-machine, etc.

---

## 0. Setup & automated checks

```bash
make setup                 # venv + deps
# build whisper.cpp + download a model per README, then:
samwhispers-supervisor     # starts tray + web UI + worker
```

Automated suite (no display needed for these modules):

```bash
make check                 # ruff + mypy + pytest
# Targeted, just this session's modules:
python -m pytest tests/test_supervisor.py tests/test_webserver.py \
  tests/test_webconfig.py tests/test_history.py tests/test_translate.py \
  tests/test_overlay.py tests/test_streaming.py tests/test_models.py -q
```

Expect: these pass. ~54 failures in `test_app/test_hotkeys/test_integration`
are **pre-existing and environmental** (pynput needs an X display); they should
pass on your desktop.

---

## 1. Start-on-boot & tray  _(needs: display)_

- [ ] **[P1]** Run `samwhispers-supervisor` → a tray icon appears; worker starts.
- [ ] **[P1]** Tray menu: **Pause** stops dictation (hotkey does nothing) and
      icon turns amber; **Resume** restores it (green).
- [ ] **[P2]** **Restart worker** → dictation still works afterward.
- [ ] **[P1]** **Quit** → tray, worker, and whisper-server all exit.
- [ ] **[P2]** Install the systemd user unit (`docs/STARTUP.md`); log out/in →
      supervisor starts automatically. Check `ExecStart=` path first.
- [ ] **[P2]** Headless check: `samwhispers-supervisor --no-tray` runs without a
      tray and is stoppable with Ctrl+C / SIGTERM.

## 2. Config web UI  _(needs: display)_

- [ ] **[P1]** Open http://127.0.0.1:7891 (or tray → **Open settings**); the
      form loads current config.
- [ ] **[P1]** Change a non-whisper setting (e.g. a vocabulary word) → **Save**
      → toast says saved; worker restarts; whisper model is **not** reloaded
      (fast). _(see §3)_
- [ ] **[P1]** Enter an invalid value (e.g. hotkey mode `xyz`) → **Save** →
      400 error toast, nothing written.
- [ ] **[P2]** **Reload from disk** re-reads the file; Pause/Restart buttons and
      the status pill reflect worker state (polls ~3s).
- [ ] **[P2]** Edit `config.toml` by hand → confirm the UI round-trips it on
      next load (esp. per-language `[vocabulary.xx]`).

## 3. Supervisor owns whisper-server (warm restart)  _(needs: model)_

- [ ] **[P1]** Save a **non-whisper** change → worker restarts in well under a
      second; transcription works immediately (model stayed warm). Response
      includes `whisper_restarted: false`.
- [ ] **[P1]** Change a **whisper** setting (model path / languages / managed)
      → **Save** → whisper-server reloads (brief pause), `whisper_restarted:
      true`, then transcription works with the new setting.

## 4. Transcription history  _(needs: audio + model)_

- [ ] **[P1]** Dictate a few times → **History** tab lists them, newest first,
      with language/duration.
- [ ] **[P1]** Search filters entries; **Copy** copies text; **Delete** removes
      one; **Clear all** empties the list.
- [ ] **[P2]** Set `history.max_entries` low, dictate past it → oldest pruned.
- [ ] **[P2]** Set `history.enabled = false` → new dictations not stored.

## 5. Translation  _(needs: audio + model + API key)_

- [ ] **[P1]** Set up an OpenAI/Anthropic key in **AI Cleanup**, enable
      **Translation**, target = `fr`. Dictate English → French text is injected.
- [ ] **[P1]** History shows the original **and** the translation (second line).
- [ ] **[P2]** Wrong/empty key → original (untranslated) text is injected
      (graceful fallback), no crash.

## 6. On-screen overlay  _(needs: display + audio)_

- [ ] **[P1]** Start dictating → translucent pill appears bottom-center with
      white bars; bars **react to your voice** (not pegged at max).
- [ ] **[P1]** On release → bars become a **spinner** while transcribing, then
      the pill disappears.
- [ ] **[P2]** `overlay.enabled = false` → no overlay.
- [ ] **[P2]** Over SSH / no display → no crash; overlay silently skipped.

## 7. Streaming transcription  _(needs: audio + model)_

- [ ] **[P1]** Enable **Streaming**, engine `chunked`, mode `preview`. Speak a
      sentence → live text appears in the **widened overlay panel**, updating as
      you talk; on release the final (cleaned/translated) paragraph is injected.
- [ ] **[P1]** Mode `progressive` → stable words are **typed into the app as you
      speak**; overlay stays the compact pill (no text panel); tail flushes on
      release.
- [ ] **[P2]** Tune `interval_seconds`; confirm partials feel responsive and the
      committed prefix doesn't flicker.
- [ ] **[P2]** _engine `faster_whisper`_: `pip install samwhispers[faster-whisper]`,
      pick a model → streaming works via faster-whisper.
- [ ] **[P2]** Enable streaming with faster-whisper **without** installing it →
      validation warning, falls back to batch (no crash).
- [ ] **[P1]** Streaming **disabled** (default) → batch behaviour unchanged.

## 8. Model selection & download  _(needs: display; download needs network)_

- [ ] **[P1]** Whisper **Model** dropdown lists `ggml-*.bin` files found on disk;
      selecting one fills the path field. Custom path still editable.
- [ ] **[P1]** **Download model**: pick a model → **Download** → live progress
      (MB / %); on completion it's selected and added to the dropdown. Click
      **Save** to apply.
- [ ] **[P2]** Start a second download while one runs → rejected (409).
- [ ] **[P2]** faster-whisper model field shows a dropdown of standard sizes
      (only when that engine is selected).

## 9. Polish / conditional UI  _(needs: display)_

- [ ] **[P2]** Streaming fields are hidden until **Streaming → Enabled**; the
      faster-whisper model/compute fields appear only for that engine.
- [ ] **[P2]** Translation target dropdown excludes `auto`.

---

## Notes for triage

- Anything visual (tray, overlay, UI) and anything needing audio/model was
  **not** verifiable in the build environment — these are the highest-value
  manual checks.
- If a streaming partial feels laggy or jumpy, note the engine + model +
  `interval_seconds`; that's a tuning conversation, not necessarily a bug.
