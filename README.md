# luxstatus

Drives a Luxafor Flag from your Outlook calendar, microphone/camera use and a manual override. Windows only; no cloud, no Graph API, no Luxafor software. See [`docs/SPEC.md`](docs/SPEC.md) and [`docs/PLAN.md`](docs/PLAN.md).

## Status

Milestones 0–4 are done: the app follows Teams calls and has a tray menu and hotkeys. Milestones 5 (Outlook) and 6 (lock detection, single instance, exe, start at login) are written and awaiting checks on the real machine. Milestone 7 is polish.

## What the light shows

Highest priority first:

| Situation | Light |
|---|---|
| Manual override from the tray or a hotkey | DND magenta, Available green, Off |
| Screen locked | Off (even during a call) |
| Mic or camera in use by Teams, Zoom, Webex or a browser | Red |
| Outside work hours (Mon–Fri 07:30–17:00 by default) | Off |
| Outlook meeting marked Busy or Out of office | Red |
| Outlook meeting marked Tentative | Amber |
| Otherwise | Green |

Classic Outlook must be running for the calendar to count; if it is closed, the calendar is ignored after about 90 s.

## Install

1. Build the exe (or download `luxstatus-exe` from the CI run):
   ```powershell
   ./scripts/build.ps1          # produces dist\luxstatus.exe
   ```
2. Copy `dist\luxstatus.exe` somewhere permanent, e.g. `%LOCALAPPDATA%\Programs\luxstatus\`.
3. Optional: copy `config.toml` to `%APPDATA%\luxstatus\config.toml` and edit it. Every key is optional.
4. Run the exe, right-click the tray icon and tick **Start at login**.

Only one copy runs at a time; starting a second one shows a message and exits.

## Use

- The tray icon matches the light; hover for the reason, e.g. `CALL — MSTeams (mic + camera)` or `BUSY — Outlook: Weekly sync until 14:30`.
- Menu: Auto, Do not disturb (30 minutes / 1 hour / Until cleared), Available, Off, Start at login, Open log, Quit. Quit turns the light off.
- Hotkeys: `Ctrl+Alt+D` toggles DND, `Ctrl+Alt+A` returns to Auto (change them under `[hotkeys]`).
- Logs: `%LOCALAPPDATA%\luxstatus\luxstatus.log`.

Config is read from the first of: `--config PATH`, `%APPDATA%\luxstatus\config.toml`, `config.toml` in the current folder, `config.toml` next to the exe. Otherwise built-in defaults apply. The log says which file was used. Changes need a restart (Quit, then start it again).

Command-line options: `--console` (no tray; Ctrl+C quits), `-v` (debug logging), `--install-startup` / `--uninstall-startup`, `--check` (verify dependencies and exit).

## Development

```powershell
uv sync
uv run pytest
uv run ruff check .
uv run python -m luxstatus            # run from source with the tray
```

The tests run on any OS; Windows APIs are imported lazily and replaced with fakes in tests. CI runs them on Ubuntu and Windows, builds the exe and runs its `--check`.

### Diagnostics

```powershell
uv run python -m luxstatus.probe [--light] [--hours 24]   # HID, mic/camera registry, Outlook
uv run python -m luxstatus.light busy                     # set a colour: status name or r,g,b
uv run python -m luxstatus.light dnd --watch              # hold a colour to test unplug/replug
```
