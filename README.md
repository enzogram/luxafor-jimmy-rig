# luxstatus

Drives a Luxafor Flag from your Outlook calendar, microphone/camera use and a manual override. Windows only; no cloud, no Graph API, no Luxafor software. See [`docs/SPEC.md`](docs/SPEC.md) and [`docs/PLAN.md`](docs/PLAN.md).

## Status

Milestones 0 (probe) and 1 (core logic) are done. The app itself is not runnable yet.

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
