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

**Probe results (2026-10-22, Windows 11, `python -m luxstatus.probe`):**

- HID: one interface, `LUXAFOR FLAG`, usage page `0xFF00`; 9-byte static-colour reports are accepted.
- Registry: new Teams is the packaged key `MSTeams_8wekyb3d8bbwe` under both `microphone` and `webcam`. During a test call both showed `LastUsedTimeStop = 0`, and the devices source reported `CALL — MSTeams (mic + camera)`.
- Outlook: classic Outlook is running, locale `English_United States`. The `Restrict` filter (`MM/dd/yyyy hh:mm AM/PM`) returned the next day's items correctly, and item times came through as local wall-clock times once the tzinfo is stripped. A Free item (`Enter Time Sheet`) is dropped as intended.

## Architecture

```
luxstatus/
  pyproject.toml
  config.toml            # colours, poll intervals, work hours, hotkeys, app filters
  luxstatus/
    main.py              # wiring, startup, shutdown
    config.py            # config.toml loading + defaults
    state.py             # Status enum + resolver (priority logic)
    engine.py            # asyncio loop: queue -> resolver -> light
    light.py             # Luxafor HID driver
    probe.py             # one-shot hardware/OS diagnostics
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
- **Outlook poller:** a dedicated, long-lived thread (not `asyncio.to_thread`, whose pool threads vary between calls). It calls `pythoncom.CoInitialize()` once, keeps its COM objects on that thread, and runs a blocking poll loop.
- **Hotkeys:** a dedicated thread that registers hotkeys with `RegisterHotKey` and pumps its own message loop.
- **Communication:** sources push `SourceUpdate`s onto an `asyncio.Queue`. Sources on other threads (Outlook, hotkeys, tray menu callbacks) must use `loop.call_soon_threadsafe(queue.put_nowait, update)`. The resolver recomputes the status on every update and every tick, and writes to the light only on change.
- **Single instance:** a named mutex at startup; a second instance exits immediately so two processes never fight over the light.

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

1. Manual override, if set and not expired. Can be DND, AVAILABLE, or OFF. Manual OFF replaces the separate "Pause light" idea.
2. Session locked → OFF, even during a call.
3. Devices: CALL. A live call overrides work hours, so evening calls still show.
4. Outside work hours → OFF.
5. Outlook: BUSY (BusyStatus 2 or 3) > TENTATIVE (1). Working Elsewhere (4) counts as AVAILABLE.
6. AVAILABLE.

The resolver is a pure function of the latest update from each source, the current time and the config. Stateful logic (debounce, override bookkeeping) lives in the sources.

### Stale data

- If a polled source (devices, Outlook, session) has not reported within 3× its interval, treat its state as unknown and skip it. The manual source is event-driven and never goes stale; its expiry is checked instead.
- Never let a crashed source pin the light red.
- A desktop app that crashes mid-call can leave `LastUsedTimeStop` at `0` indefinitely; see the process check under devices.

## Components

### light.py — Luxafor driver

- Uses the `hidapi` package (imported as `hid`). Do not also install the unrelated `hid` package, which uses the same import name.
- Opens by VID/PID. On any write error, close the device and retry the open on the next tick, which handles unplug/replug and sleep/resume.
- After every successful (re)open, resend the current status: a replugged Flag comes back dark.
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

- Poll every 30–60 s for a look-ahead window (now → now + 4 h by default) and report the list of calendar blocks. The resolver decides which block covers "now" on every 5 s tick, so meeting starts and ends are on time rather than up to one poll late.
- Take the busiest `BusyStatus` among blocks covering now. Skip all-day FREE items and cancelled meetings (`MeetingStatus` 5 or 7).
- With `IncludeRecurrences = True`, `items.Count` is meaningless (2147483647). Iterate with `GetFirst()` / `GetNext()` only.
- The `Restrict` date format follows the Windows locale. Build it with `strftime('%m/%d/%Y %I:%M %p')` and confirm it with the probe on the real machine.
- pywin32 returns appointment times tagged as UTC although they are local wall-clock times. Strip the tzinfo and treat them as naive local times; the probe prints both to confirm.
- If Outlook is not running, `Dispatch` will launch it, which is unwanted. Instead, check for a running `OUTLOOK.EXE` first (via `psutil`). Report unknown if it is not running.
- After sleep/resume or any COM error, drop the cached Outlook objects and reconnect on the next poll.
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
- **Debounce:** require 2 consecutive "in use" reads before reporting CALL, and ~10 s of "not in use" before clearing it. This rides out brief device grabs and call reconnects. The debouncer lives in this source, not in the resolver.
- **Stuck entries:** for NonPackaged apps the key name is the exe path. Ignore an entry whose exe is not currently running (checked with `psutil`), so a crashed app cannot pin CALL.
- **App filter** (from config, case-insensitive):
  - `include`: substrings to match. Default `["MSTeams", "zoom", "webex", "chrome", "msedge", "firefox"]`. An empty list means any app. Including browsers means any website using the mic counts as a call; this is deliberate.
  - `exclude`: substrings for false positives, such as dictation or voice-recorder apps.
- **Detail string:** the tooltip shows which app triggered CALL and whether the camera is also on.
- Camera-on can optionally use a different colour or effect (`camera_distinct` in config).

### sources/manual.py + hotkeys.py

- **Tray menu:** Auto, DND (30 min / 1 h / until cleared), Available, Off.
- **Global hotkeys** via Win32 `RegisterHotKey`, set in config. For example, `ctrl+alt+d` toggles DND and `ctrl+alt+a` returns to Auto. `RegisterHotKey` avoids a system-wide keyboard hook, which security software tends to flag as a keylogger. If a hotkey is already taken, log it and carry on.
- The override expiry is checked by the resolver tick, every 5 s.

### sources/session.py (optional)

- Turn the light OFF when the workstation is locked. Detect this by polling whether `OpenInputDesktop` succeeds (it fails while the lock screen is up), every 2 s.
- Work hours come from config, e.g. Mon–Fri 07:30–17:00, and are applied by the resolver.

### tray.py

- The pystray icon colour matches the current status.
- The tooltip shows the status and its reason, e.g. "CALL — MSTeams (mic + camera)" or "BUSY — Outlook: Weekly TSF sync until 14:30".
- Menu: manual options, "Open log", "Quit". On Quit, set the light to OFF.

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
lookahead_minutes = 240

[session]
enabled = true
poll_seconds = 2

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

`hidapi`, `pywin32`, `pystray`, `Pillow`, `psutil`; stdlib `winreg` and `tomllib` (3.11+). Managed with `uv`; packaged with PyInstaller.

## Run at login

- Build a single windowed exe with PyInstaller and put a shortcut to it in `shell:startup`.
- Logs go to `%LOCALAPPDATA%\luxstatus\luxstatus.log` (rotating). A windowed exe has no console, so log only to the file and install a `sys.excepthook` that writes crashes to the log.

## Milestones

See `docs/PLAN.md`.

## Testing

- The resolver is a pure function `(source states, now, config) → Status`. Unit-test the priority, staleness, and expiry cases. Test the debouncer separately in the devices source.
- Provide a fake light class that records calls, so tests run without hardware.
- Device parser tests: inject a dict that mimics the registry tree (packaged and NonPackaged entries, in-use and idle) instead of reading the real registry.
