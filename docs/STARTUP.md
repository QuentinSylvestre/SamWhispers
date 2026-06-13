# Running SamWhispers on login

SamWhispers can run in the background and start automatically when you log in,
with a system tray icon showing whether it's running.

## How it works

`samwhispers-supervisor` is a small parent process that:

- shows a **tray icon** (green = running, amber = paused, grey = stopped) with a
  menu to **Pause/Resume**, **Restart worker**, and **Quit**;
- spawns the actual voice-to-text daemon (`samwhispers worker`) as a child
  and restarts it automatically if it crashes;
- owns the managed `whisper-server` (when `whisper.managed = true`), so the
  worker can be restarted after a config change without reloading the whisper
  model. The model is only reloaded when `[whisper]` settings themselves change.

Run it manually to try it out:

```bash
samwhispers-supervisor            # with tray icon
samwhispers-supervisor --no-tray  # headless (e.g. over SSH / no display)
```

The tray needs `pystray` and `Pillow` (installed automatically with the
package). On a host with no display the supervisor logs a warning and runs
headless.

## Easiest: the config UI

The simplest way to toggle start-at-login is the **Startup** section in the
config UI (open `http://127.0.0.1:7891`, or the tray's *Open settings*). Flip
"Start at login" — it applies immediately. The CLI below does the same thing.

## The autostart command

On Linux and Windows, one command sets up login autostart for you:

```bash
samwhispers-autostart enable     # configure it to run at every login
samwhispers-autostart disable    # stop running at login
samwhispers-autostart start      # start a background instance now (no terminal)
samwhispers-autostart stop       # stop the background instance
samwhispers-autostart status     # check it
```

`enable`/`disable` only manage the autostart entry — they don't launch or kill
the app. To run it now, use `samwhispers-supervisor` (foreground, with logs) or
`samwhispers-autostart start` (background, no window). After `enable` it comes up
automatically at every login; quit it anytime from the tray's **Quit**.

It installs a systemd *user* service on Linux and a per-user **Startup-folder
shortcut** on Windows (running via `pythonw`, no console window). The Startup
folder needs no admin rights and avoids Task Scheduler, which is often blocked
on managed/corporate machines. The manual instructions below are the fallback /
for customization (and macOS).

## Linux (systemd user service)

A user service starts on login and has access to your graphical session
(display, audio, clipboard) — that's what the worker needs.

1. Install the unit:

   ```bash
   mkdir -p ~/.config/systemd/user
   cp packaging/systemd/samwhispers.service ~/.config/systemd/user/
   ```

2. Check the `ExecStart=` path in that file points at your installed
   `samwhispers-supervisor` (run `which samwhispers-supervisor` to find it; for
   a virtualenv use the full `.venv/bin/...` path).

3. Make sure the user manager knows your display (most desktops do this
   automatically; if the worker can't grab the hotkey, run this once and add it
   to your login script):

   ```bash
   systemctl --user import-environment DISPLAY XAUTHORITY
   ```

4. Enable and start it:

   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now samwhispers.service
   ```

Useful commands:

```bash
systemctl --user status samwhispers.service
journalctl --user -u samwhispers.service -f   # follow logs
systemctl --user restart samwhispers.service
systemctl --user disable --now samwhispers.service
```

If your desktop doesn't reach `graphical-session.target`, change both
`After=` and `WantedBy=` in the unit to `default.target`.

## macOS (launchd)

Create `~/Library/LaunchAgents/com.samwhispers.supervisor.plist` (adjust the
`samwhispers-supervisor` path to match `which samwhispers-supervisor`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.samwhispers.supervisor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/samwhispers-supervisor</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

Load it:

```bash
launchctl load ~/Library/LaunchAgents/com.samwhispers.supervisor.plist
```

(Use `launchctl unload ...` to stop it.)

## Windows (Task Scheduler)

The simplest approach is Task Scheduler with an "At log on" trigger:

1. Open **Task Scheduler** -> **Create Task**.
2. **Triggers** -> **New** -> *Begin the task:* **At log on**.
3. **Actions** -> **New** -> *Program/script:* the full path to
   `samwhispers-supervisor.exe` (inside your Python/venv `Scripts` folder).
4. Save. It will start the supervisor (with tray icon) each time you log in.

Alternatively, drop a shortcut to `samwhispers-supervisor.exe` in the Startup
folder (`shell:startup`).
