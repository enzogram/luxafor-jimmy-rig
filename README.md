# luxstatus

Drives a Luxafor Flag from your Outlook calendar, microphone/camera use and a manual override. Windows only; no cloud, no Graph API, no Luxafor software. See [`docs/SPEC.md`](docs/SPEC.md) and [`docs/PLAN.md`](docs/PLAN.md).

## Status

Milestones 0–3 are done: the app follows Teams calls. Milestone 4 (tray, manual override, hotkeys) is written and awaiting a manual check. Outlook and lock detection come next.

## Development

```powershell
uv sync
uv run pytest
uv run ruff check .
```

The tests run on any OS; Windows APIs are imported lazily and replaced with fakes in tests.

## Probe (milestone 0)

Run this on the Windows machine with the Flag plugged in and classic Outlook open:

```powershell
uv run python -m luxstatus.probe            # read-only checks
uv run python -m luxstatus.probe --light    # also flash the Flag green, red, then off
```

Run it once while idle and once during a Teams test call, and send back both outputs. It checks:

1. **HID** — whether the Flag enumerates at VID `0x04D8`, PID `0xF372`.
2. **Mic/camera registry** — every ConsentStore entry, which ones read as in use, and which ones luxstatus would report as a call with the default filters.
3. **Outlook** — whether classic Outlook is running, the `Restrict` filter string, and the calendar items for the next few hours as luxstatus sees them.

## Light CLI (milestone 2)

```powershell
uv run python -m luxstatus.light busy              # status name: off, available, tentative, busy, call, dnd
uv run python -m luxstatus.light 0,0,255           # or r,g,b
uv run python -m luxstatus.light dnd --watch       # hold the colour; Ctrl+C turns it off
```

With `--watch`, unplug the Flag and plug it back in: the colour should return within about 5 seconds.

## Run

```powershell
uv run python -m luxstatus             # tray icon; right-click for the menu
uv run python -m luxstatus --console   # no tray; Ctrl+C quits
uv run python -m luxstatus -v          # debug logging
```

- The tray icon matches the light; hover for the reason, e.g. `CALL — MSTeams (mic + camera)`.
- Menu: Auto, Do not disturb (30 minutes / 1 hour / Until cleared), Available, Off, Open log, Quit. Quit turns the light off.
- Hotkeys: `Ctrl+Alt+D` toggles DND, `Ctrl+Alt+A` returns to Auto (change them under `[hotkeys]`).
- Logs: `%LOCALAPPDATA%\luxstatus\luxstatus.log`.

A call shows at any time of day; otherwise the light is off outside work hours (Mon–Fri 07:30–17:00 by default; adjust under `[work_hours]`, or set `enabled = false` there to test in the evening). Manual overrides apply at any time.
