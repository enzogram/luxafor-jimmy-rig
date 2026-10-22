# luxstatus — Luxafor Flag status light

Local Python service that drives a Luxafor Flag from three signals: Outlook calendar state, microphone/camera use, and a manual override. No cloud, no Graph API, no Teams integration, no Luxafor software.

## Goals

- Light reflects "can I be interrupted?" within ~30 s of a calendar change and ~5 s of a call starting or ending.
- Runs at login, lives in the system tray, survives Outlook restarts and USB unplug/replug.
- Each status source is optional; if one is unavailable, the others still work.

## Non-goals

- Mirroring Teams presence (Available/Away/DND). It is not reachable without Graph consent.
- Detecting mute state. Muting inside Teams usually keeps the microphone open, so the device stays in use.
- Supporting new Outlook. It has no COM interface; this tool needs classic Outlook.

## Before building, verify

1. **Classic Outlook is installed and running.** If only new Outlook is available, drop the calendar source or replace it with an ICS feed, if publishing one is allowed.
2. **The Flag enumerates as HID.** Expect VID `0x04D8`, PID `0xF372`. Check with `python -c "import hid; print(hid.enumerate(0x04D8, 0xF372))"`.
3. **The mic registry key changes during a call.** Start a Teams test call, then run:
   `reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone" /s`
   Note the exact Teams subkey name and confirm its `LastUsedTimeStop` value is `0` while the call is live.

## Architecture

```
luxstatus/
  pyproject.toml
  config.toml            # colours, poll intervals, work hours, hotkeys, app filters
  luxstatus/
    main.py              # wiring, startup, shutdown
    state.py             # Status enum + resolver (priority logic)
    light.py             # Luxafor HID driver
    tray.py              # pystray icon + menu
    hotkeys.py           # global hotkeys -> manual override
    sources/
      outlook.py         # calendar poller (COM)
      devices.py         # mic/camera-in-use poller (registry)
      manual.py          # override state + expiry
      session.py         # screen lock / work hours (optional)
  tests/
```

### Threading model

- **Main thread:** the pystray event loop, which must run on the main thread on Windows.
- **Worker thread:** an asyncio loop that hosts the resolver, the device poller, and the light writer.
- **Outlook poller:** runs in its own thread via `asyncio.to_thread`. Call `pythoncom.CoInitialize()` once per thread; COM objects must not cross threads.
- **Communication:** sources push `SourceUpdate(source, status, detail, ts)` onto an `asyncio.Queue`. The resolver recomputes the status and writes to the light only on change.

## Status model

```python
class Status(Enum):
    OFF        # outside work hours, screen locked, or user turned it off
    AVAILABLE
    TENTATIVE  # tentative calendar block
    BUSY       # busy calendar block / OOF
    CALL       # mic (or camera) in use by a filtered app
    DND        # manual
```

### Resolver priority (highest wins)

1. Manual override, if set and not expired. Can be DND, AVAILABLE, or OFF.
2. Session locked, or outside work hours → OFF. A live CALL overrides work hours, so evening calls still show.
3. Devices: CALL.
4. Outlook: BUSY (BusyStatus 2 or 3) > TENTATIVE (1). Working Elsewhere (4) counts as AVAILABLE.
5. AVAILABLE.

### Stale data

- If a source has not reported within 3× its interval, treat its state as unknown and skip it.
- Never let a crashed source pin the light red.

## Components

### light.py — Luxafor driver

- Uses `hidapi` (`pip install hidapi`).
- Opens by VID/PID. On any write error, close the device and retry the open on the next tick, which handles unplug/replug.
- On Windows, writes are prefixed with report ID `0x00`.
- Command bytes, from the community Luxafor protocol docs (verify against the device):

| Command | Bytes |
|---|---|
| Static colour | `[0x01, 0xFF, r, g, b, 0, 0, 0]` (`0xFF` = all LEDs) |
| Fade | `[0x02, 0xFF, r, g, b, speed, 0, 0]` |
| Strobe | `[0x03, 0xFF, r, g, b, speed, 0, repeat]` |
| Off | static `(0, 0, 0)` |

- API: `set(status)`, which maps status → colour/effect via config.
- Optional effect: a short strobe when a call starts.

### sources/outlook.py — calendar poller

```python
import win32com.client, pythoncom
ns  = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
cal = ns.GetDefaultFolder(9)            # olFolderCalendar
items = cal.Items
items.IncludeRecurrences = True         # must be set before Sort
items.Sort("[Start]")
restricted = items.Restrict(
    "[Start] < '{end}' AND [End] > '{start}'"
)  # dates formatted 'MM/dd/yyyy hh:mm AM/PM'
```

- Poll every 30–60 s for the window now → now + 1 min. Take the busiest `BusyStatus` among overlapping items, excluding all-day FREE items.
- If Outlook is not running, `Dispatch` will launch it, which is unwanted. Instead, check for a running `OUTLOOK.EXE` first (via `psutil`) or use `GetActiveObject`. Report unknown if it is not running.
- Reading appointments does not trigger the Outlook object-model security prompt; only address/recipient access does.

### sources/devices.py — mic/camera in use

Reads the Windows privacy "app is using your microphone/camera" state from the registry. Uses `winreg` from the stdlib; needs no extra permissions.

- **Roots:**
  - `HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone`
  - `HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam`
- **Packaged apps** (new Teams, Store apps) are direct subkeys, e.g. `MSTeams_8wekyb3d8bbwe`.
- **Desktop apps** (Zoom, browsers, classic apps) are under the `NonPackaged` subkey. Their key names are executable paths with `\` replaced by `#`.
- **In use:** an app is in use when `LastUsedTimeStart` > 0 and `LastUsedTimeStop` == 0. Both values are FILETIME integers.
- **Polling:** poll every 2–3 s. Registry reads are cheap, so there is no need for change notifications. `RegNotifyChangeKeyValue` is an option later if wanted.
- **Debounce:** require 2 consecutive "in use" reads before reporting CALL, and ~10 s of "not in use" before clearing it. This rides out brief device grabs and call reconnects.
- **App filter** (from config):
  - `include`: substrings to match. Default `["MSTeams", "zoom", "webex", "chrome", "msedge", "firefox"]`. An empty list means any app.
  - `exclude`: substrings for false positives, such as dictation or voice-recorder apps.
- **Detail string:** the tooltip shows which app triggered CALL and whether the camera is also on.
- Camera-on can optionally use a different colour or effect (`camera_distinct` in config).

### sources/manual.py + hotkeys.py

- **Tray menu:** Auto, DND (30 min / 1 h / until cleared), Available, Off.
- **Global hotkeys** via `pynput`, set in config. For example, `ctrl+alt+d` toggles DND and `ctrl+alt+a` returns to Auto.
- The override expiry is checked by the resolver tick, every 5 s.

### sources/session.py (optional)

- Turn the light OFF when the workstation is locked. Detect this either by polling for the `LogonUI.exe` process or by registering `WTSRegisterSessionNotification` on a hidden window.
- Work hours come from config, e.g. Mon–Fri 07:30–17:00.

### tray.py

- The pystray icon colour matches the current status.
- The tooltip shows the status and its reason, e.g. "CALL — MSTeams (mic + camera)" or "BUSY — Outlook: Weekly TSF sync until 14:30".
- Menu: manual options, "Pause light", "Open log", "Quit". On Quit, set the light to OFF.

## config.toml

```toml
[colours]
available = [0, 255, 0]
tentative = [255, 180, 0]
busy      = [255, 0, 0]
call      = [255, 0, 0]
call_camera = [255, 0, 0]   # used only if camera_distinct = true
dnd       = [255, 0, 255]

[work_hours]
days = ["Mon","Tue","Wed","Thu","Fri"]
start = "07:30"
end   = "17:00"

[outlook]
enabled = true
poll_seconds = 30

[devices]
enabled = true
poll_seconds = 2
on_debounce_reads = 2
off_debounce_seconds = 10
camera_distinct = false
include = ["MSTeams", "zoom", "webex", "chrome", "msedge", "firefox"]
exclude = []

[hotkeys]
dnd  = "<ctrl>+<alt>+d"
auto = "<ctrl>+<alt>+a"
```

## Dependencies

`hidapi`, `pywin32`, `pystray`, `Pillow`, `pynput`, `psutil`; stdlib `winreg` and `tomllib` (3.11+)

## Run at login

- Put a shortcut to `pythonw.exe -m luxstatus` in `shell:startup`.
- Optional: build a single exe with PyInstaller.
- Logs go to `%LOCALAPPDATA%\luxstatus\luxstatus.log` (rotating).

## Milestones

1. **Light driver:** CLI sets colours; unplug/replug recovery works.
2. **Device source + resolver:** the light follows a test call. Test the resolver with a fake source that injects states.
3. **Outlook source.**
4. **Tray + manual override + hotkeys.**
5. **Session lock + work hours; startup shortcut.**
6. **Polish:** transition effects, tooltip reasons, config reload without restart.

## Testing

- The resolver is a pure function `(source states, now, config) → Status`. Unit-test the priority, staleness, debounce, and expiry cases.
- Provide a fake light class that records calls, so tests run without hardware.
- Device parser tests: inject a dict that mimics the registry tree (packaged and NonPackaged entries, in-use and idle) instead of reading the real registry.
