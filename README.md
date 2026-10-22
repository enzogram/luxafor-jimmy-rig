# luxstatus

Drives a Luxafor Flag from your Outlook calendar, microphone/camera use and a manual override. Windows only; no cloud, no Graph API, no Luxafor software. See [`docs/SPEC.md`](docs/SPEC.md) and [`docs/PLAN.md`](docs/PLAN.md).

## Status

Milestones 0–2 (probe, core logic, light driver) are done. Milestone 3 runs the app headless in a console with mic/camera detection only; it is awaiting a Teams call test. Outlook, tray, hotkeys and lock detection come next.

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

## Run (milestone 3, console)

```powershell
uv run python -m luxstatus          # Ctrl+C quits and turns the light off
uv run python -m luxstatus -v       # debug logging
```

Each status change prints a line such as `17:42:10  CALL — MSTeams (mic + camera)`. A call shows at any time of day; otherwise the light is off outside work hours (Mon–Fri 07:30–17:00 by default; set `[work_hours] enabled = false` in `config.toml` to test in the evening).
