# Manual tests still to run

Checks that need the real machine (Flag, Outlook, Windows session). Tick them off as they pass; once a milestone's list is done, mark it ✅ in `PLAN.md`.

## Before you start

```powershell
git checkout main
git pull
uv sync
```

Run from source with `uv run python -m luxstatus` (tray) or `uv run python -m luxstatus --console -v` (live log in the terminal, most useful when something looks wrong). For the exe checks, build it with `./scripts/build.ps1`.

## Reporting back

Send this file's checklist with each item marked pass or fail, plus a note on anything that looked odd. For each failure, include:

- **What you did and what you saw** (light colour, tooltip text, any notification).
- **The log:** tray → Open log, or `%LOCALAPPDATA%\luxstatus\luxstatus.log`. The lines around the time of the failure are enough; it contains meeting subjects, so trim anything private.
- **The version:** output of `git log -1 --oneline`, or say you used the exe from CI.
- For Outlook problems, the Outlook section of `uv run python -m luxstatus.probe --hours 24`.

## Milestone 5 — Outlook calendar

Run `uv run python -m luxstatus` or the exe. After 17:00, set `enabled = false` under `[work_hours]` first.

- [ ] A test appointment covering now, shown as **Busy**, turns the light red within ~30 s; the tooltip reads `BUSY — Outlook: <subject> until HH:MM`.
- [ ] Changing it to **Tentative** turns the light amber within ~30 s.
- [ ] Deleting it returns the light to green.
- [ ] During a Busy meeting, a Teams call shows `CALL — MSTeams` (call wins).
- [ ] Optional: closing Outlook during a meeting returns the light to green after ~90 s; the log says "Outlook not running; calendar unknown".
- [ ] `uv run python -m luxstatus.probe --hours 24` lists today's and tomorrow's meetings with the right times, marks the current one "covers now", and shows `calendar now: BUSY`.

## Milestone 6 — exe, lock, single instance, start at login

Build with `./scripts/build.ps1` (or download `luxstatus-exe` from CI) and copy `dist\luxstatus.exe` to e.g. `%LOCALAPPDATA%\Programs\luxstatus\`. Copy an edited `config.toml` to `%APPDATA%\luxstatus\config.toml` if you have one.

- [ ] The exe starts with a tray icon and no console window.
- [ ] "Open log" shows which config file was loaded.
- [ ] Starting the exe a second time shows "luxstatus is already running" and exits.
- [ ] Win+L turns the light off within ~2 s; unlocking restores it.
- [ ] Ticking "Start at login", then signing out and back in, starts the app automatically.
- [ ] Unticking it removes the shortcut (`shell:startup` no longer has `luxstatus.lnk`).
- [ ] Signing out (or restarting) while the light is on is not blocked by luxstatus, and the light turns off.

## Milestone 7 — polish

- [ ] When a call starts, the light strobes for about 2 s, then stays solid red. If the strobe looks wrong (too fast, too long, ends dark), note what you saw; `flash_speed`, `flash_repeat` and `flash_seconds` under `[effects]` tune it.
- [ ] Camera on/off during a call does not re-trigger the flash.
- [ ] Tray → **Open config** opens `%APPDATA%\luxstatus\config.toml` (created from the template the first time).
- [ ] Changing `busy` under `[colours]` and saving recolours the light and tray icon within ~2 s during a Busy meeting, with no restart.
- [ ] Saving a broken file (e.g. delete a `]`) shows a tray notification and the light keeps working; fixing it applies the new settings.
- [ ] Changing a hotkey under `[hotkeys]` takes effect without a restart.
