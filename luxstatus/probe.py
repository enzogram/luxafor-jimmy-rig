"""One-shot diagnostics for the spec's "before building, verify" checks.

    uv run python -m luxstatus.probe [--light] [--hours N]

Each section runs independently and reports its own failure, using the same adapter code
the app will use.
"""

from __future__ import annotations

import argparse
import locale
import platform
import sys
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from luxstatus.config import Config, DevicesConfig
from luxstatus.light import OFF, PRODUCT_ID, VENDOR_ID, static_report
from luxstatus.sources import devices, outlook
from luxstatus.state import Source, SourceUpdate, calendar_status, resolve


def filetime(ft: int) -> str:
    """FILETIME (100 ns ticks since 1601, UTC) → local time string."""
    if not ft:
        return "0"
    utc = datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=ft // 10)
    return utc.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def section(title: str, fn: Callable[[], None]) -> bool:
    print(f"\n=== {title} ===")
    try:
        fn()
        return True
    except Exception:
        traceback.print_exc(file=sys.stdout)
        return False


def probe_hid(flash: bool) -> None:
    import hid

    found = hid.enumerate(VENDOR_ID, PRODUCT_ID)
    print(f"{len(found)} interface(s) at {VENDOR_ID:#06x}:{PRODUCT_ID:#06x}")
    for d in found:
        path = d.get("path")
        print(
            f"  path={path!r} product={d.get('product_string')!r} "
            f"usage_page={d.get('usage_page')} usage={d.get('usage')} "
            f"interface={d.get('interface_number')}"
        )
    if not found:
        print("  Flag not found. All HID devices:")
        for d in hid.enumerate():
            print(f"    {d['vendor_id']:#06x}:{d['product_id']:#06x} {d.get('product_string')!r}")
        return
    if flash:
        dev = hid.device()
        dev.open(VENDOR_ID, PRODUCT_ID)
        try:
            for name, rgb in (("green", (0, 255, 0)), ("red", (255, 0, 0)), ("off", OFF)):
                n = dev.write(static_report(rgb))
                print(f"  wrote {name}: {n} bytes")
                time.sleep(1.5)
        finally:
            dev.close()


def probe_devices() -> None:
    cfg = DevicesConfig()
    running = devices.running_process_names()
    for capability in (devices.MICROPHONE, devices.WEBCAM):
        tree = devices.read_consent_tree(capability)
        entries = devices.parse_consent_tree(tree)
        print(f"{capability}: {len(entries)} app entries")
        for e in sorted(entries, key=lambda e: (not e.in_use, e.packaged, e.key.lower())):
            flags = []
            if e.in_use:
                flags.append("IN USE")
            if not e.packaged:
                flags.append("running" if e.name.lower() in running else "not running")
            if devices.matches(e, cfg.include, cfg.exclude):
                flags.append("matches filter")
            print(
                f"  {'*' if e.in_use else ' '} {e.key}\n"
                f"      start={filetime(e.start)} stop={filetime(e.stop)} [{', '.join(flags)}]"
            )
    src = devices.DevicesSource(DevicesConfig(on_debounce_reads=1))
    u = src.poll(datetime.now())
    print(f"\nluxstatus would report: {u.status.name if u.status else 'no call'} {u.detail}")


def probe_outlook(hours: float) -> None:
    import pythoncom

    print(f"locale: {locale.getlocale()}")
    running = outlook.outlook_running()
    print(f"OUTLOOK.EXE running: {running}")
    if not running:
        print("  Start classic Outlook and run the probe again.")
        return
    now = datetime.now()
    end = now + timedelta(hours=hours)
    print(f"Restrict filter: {outlook.restrict_filter(now, end)}")
    pythoncom.CoInitialize()
    try:
        client = outlook.OutlookClient()
        items = client.fetch(now, end) or []
        print(f"{len(items)} item(s) in the next {hours:g} h (times should match Outlook's):")
        for i in items:
            print(
                f"  {i.start:%a %H:%M}-{i.end:%H:%M} busy={i.busy_status} "
                f"all_day={i.all_day} meeting_status={i.meeting_status} {i.subject!r}"
            )
        blocks = outlook.to_blocks(items)
        status, block = calendar_status(blocks, now)
        print(
            f"\ncalendar now: {status.name if status else 'nothing'}"
            + (f" ({block.subject} until {block.end:%H:%M})" if block else "")
        )
        update = SourceUpdate(Source.OUTLOOK, now, blocks=blocks)
        resolution = resolve({Source.OUTLOOK: update}, now, Config())
        print(f"resolver (Outlook only): {resolution.describe()}")
    finally:
        pythoncom.CoUninitialize()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m luxstatus.probe", description=__doc__)
    parser.add_argument("--light", action="store_true", help="flash the Flag green, red, off")
    parser.add_argument("--hours", type=float, default=4, help="Outlook look-ahead (default 4)")
    args = parser.parse_args(argv)

    print(f"luxstatus probe — {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"python {sys.version.split()[0]} on {platform.platform()}")
    if sys.platform != "win32":
        print("Not Windows: the HID check may work, the registry and Outlook checks will not.")
    ok = [
        section("1. Luxafor Flag (HID)", lambda: probe_hid(args.light)),
        section("2. Microphone / camera registry", probe_devices),
        section("3. Outlook calendar", lambda: probe_outlook(args.hours)),
    ]
    print(f"\n{sum(ok)}/{len(ok)} sections ran without errors")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
