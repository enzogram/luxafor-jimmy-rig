from datetime import UTC, datetime, timedelta

from luxstatus.config import OutlookConfig
from luxstatus.sources.outlook import (
    CalendarItem,
    OutlookThread,
    com_datetime,
    restrict_filter,
    to_blocks,
)
from luxstatus.state import OL_BUSY, OL_FREE, OL_TENTATIVE, Source

T = datetime(2026, 10, 21, 9, 0)
H = timedelta(hours=1)


def test_to_blocks_drops_free_cancelled_and_empty_items():
    items = [
        CalendarItem(T, T + H, OL_BUSY, "keep"),
        CalendarItem(T, T + H, OL_TENTATIVE, "keep too"),
        CalendarItem(T, T + 24 * H, OL_FREE, "all-day free", all_day=True),
        CalendarItem(T, T + H, OL_FREE, "free"),
        CalendarItem(T, T + H, OL_BUSY, "Canceled: x", meeting_status=5),
        CalendarItem(T, T + H, OL_BUSY, "Canceled: y", meeting_status=7),
        CalendarItem(T, T, OL_BUSY, "zero length"),
    ]
    assert [b.subject for b in to_blocks(items)] == ["keep", "keep too"]


def test_all_day_busy_items_are_kept():
    items = [CalendarItem(T, T + 24 * H, OL_BUSY, "Offsite", all_day=True)]
    assert len(to_blocks(items)) == 1


def test_restrict_filter_format():
    f = restrict_filter(datetime(2026, 10, 21, 9, 5), datetime(2026, 10, 21, 13, 5))
    assert f == "[Start] < '10/21/2026 01:05 PM' AND [End] > '10/21/2026 09:05 AM'"


def test_com_datetime_strips_bogus_utc_tag():
    assert com_datetime(datetime(2026, 10, 21, 9, 30, tzinfo=UTC)) == datetime(2026, 10, 21, 9, 30)


class FakeClient:
    def __init__(self, results):
        self.results = list(results)  # each: list of items, None (not running) or an exception
        self.calls = []
        self.resets = 0

    def fetch(self, start, end):
        self.calls.append((start, end))
        r = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(r, Exception):
            raise r
        return r

    def reset(self):
        self.resets += 1


def make_thread(results, **cfg):
    posted = []
    client = FakeClient(results)
    thread = OutlookThread(
        OutlookConfig(**cfg), posted.append, client=client, clock=lambda: T, com=False
    )
    return thread, client, posted


def test_poll_posts_filtered_blocks_for_lookahead_window():
    items = [CalendarItem(T, T + H, OL_BUSY, "Sync"), CalendarItem(T, T + H, OL_FREE, "x")]
    thread, client, posted = make_thread([items], lookahead_minutes=120)
    thread.poll_once()
    assert client.calls == [(T, T + 2 * H)]
    [u] = posted
    assert (u.source, u.ts) == (Source.OUTLOOK, T)
    assert [b.subject for b in u.blocks] == ["Sync"]


def test_outlook_not_running_posts_nothing():
    thread, _, posted = make_thread([None])
    assert thread.poll_once() is None
    assert posted == []


def test_com_error_resets_and_retries_once():
    thread, client, posted = make_thread([OSError("RPC server unavailable"), []])
    thread.poll_once()
    assert client.resets == 1
    assert len(client.calls) == 2
    assert len(posted) == 1 and posted[0].blocks == ()


def test_persistent_error_posts_nothing_and_logs_once(caplog):
    thread, client, posted = make_thread([OSError("boom")])
    thread.poll_once()
    thread.poll_once()
    assert posted == []
    assert client.resets == 4
    assert sum("Outlook read failed" in r.message for r in caplog.records) == 1


def test_thread_polls_and_stops_promptly():
    import time

    thread, client, posted = make_thread([[]], poll_seconds=60)
    thread.start()
    deadline = time.monotonic() + 2
    while not posted and time.monotonic() < deadline:
        time.sleep(0.01)
    started = time.monotonic()
    thread.stop()
    assert not thread.is_alive()
    assert time.monotonic() - started < 1  # does not wait out the 60 s poll interval
    assert len(posted) == 1
    assert client.resets >= 1  # COM objects released on the thread


def test_restrict_filter_uses_given_formatter():
    f = restrict_filter(T, T + H, fmt=lambda dt: dt.strftime("%d/%m/%Y %H:%M"))
    assert f == "[Start] < '21/10/2026 10:00' AND [End] > '21/10/2026 09:00'"


def test_outlook_datetime_falls_back_to_us_order_off_windows(monkeypatch):
    from luxstatus.sources import outlook

    def broken(dt):
        raise ImportError("no win32api")

    monkeypatch.setattr(outlook, "locale_datetime", broken)
    assert outlook.outlook_datetime(T) == "10/21/2026 09:00 AM"
