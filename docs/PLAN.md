# luxstatus — execution plan

Development happens partly in a Linux environment without the Flag, Outlook or the Windows registry. The code is therefore split in two:

- **Core (cross-platform, fully unit-tested):** config, status model, resolver, engine loop, debouncer, registry-tree parsing, calendar-item logic, manual override, Luxafor report bytes. All Windows APIs are injected or imported lazily, so the core runs and tests anywhere.
- **Windows adapters (thin):** HID device, `winreg` reader, Outlook COM, session lock, `RegisterHotKey`, tray. These are verified on the real machine.

CI runs the unit tests on both `ubuntu-latest` and `windows-latest`. The Windows job also imports every module, which catches pywin32/hidapi packaging problems early.

## Milestones

| # | Milestone | Verified by |
|---|---|---|
| 0 ✅ | **Probe** — `uv run python -m luxstatus.probe` runs the three "before building" checks from the spec (HID enumeration, ConsentStore entries, upcoming Outlook items) using the real adapter code. `--light` also flashes the Flag. | Run on the Windows machine; paste the output back |
| 1 ✅ | **Core** — config, `Status`/`SourceUpdate`, pure resolver (priority, staleness, expiry), engine loop with a fake light, debouncer, registry-tree and calendar logic, manual override. | `uv run pytest` |
| 2 ✅ | **Light driver + CLI** — `python -m luxstatus.light busy`; rewrites the colour every tick and reconnects after unplug/replug. | Hardware (replug tested) |
| 3 ✅ | **Devices source** — `winreg` poller, process check, wired into the engine. | Teams call |
| 4 ✅ | **Tray + manual override + hotkeys** — pystray on the main thread, engine on a worker thread, `RegisterHotKey` thread. Also brings forward rotating file logging (for "Open log"). | Manual check |
| 5 🔶 | **Outlook source** — dedicated COM thread with look-ahead fetch; reconnects after COM errors. | Code done; needs a real meeting |
| 6 | **Session lock, work hours, single-instance mutex, logging, PyInstaller build, startup shortcut.** | Manual |
| 7 | **Polish** — call-start strobe, tooltip reasons, config reload without restart. | Manual |

## Decisions

- A locked screen turns the light OFF even during a call.
- Hotkeys use `RegisterHotKey`, not `pynput`.
- Tooling: `uv` for environments and running, PyInstaller for the shipped exe.
- Manual OFF replaces the separate "Pause light" menu item.
- All times are naive local wall-clock `datetime`s (Outlook's own convention). Around a DST change, sources may briefly look stale until their next poll; that is harmless.
