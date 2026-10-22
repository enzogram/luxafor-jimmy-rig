from datetime import UTC, datetime, timedelta

from luxstatus.sources.outlook import CalendarItem, com_datetime, restrict_filter, to_blocks
from luxstatus.state import OL_BUSY, OL_FREE, OL_TENTATIVE

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
