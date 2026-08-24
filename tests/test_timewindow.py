from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from morning_digest.timewindow import digest_window


def test_window_uses_most_recent_six_am_cutoff():
    tz = ZoneInfo("Europe/Zurich")
    start, end = digest_window(datetime(2026, 8, 12, 8, tzinfo=timezone.utc), tz)
    assert end.isoformat() == "2026-08-12T06:00:00+02:00"
    assert start.isoformat() == "2026-08-11T06:00:00+02:00"


def test_window_preserves_local_six_across_dst_change():
    tz = ZoneInfo("Europe/Zurich")
    start, end = digest_window(datetime(2026, 3, 29, 10, tzinfo=timezone.utc), tz)
    assert start.hour == end.hour == 6
    assert start.utcoffset() != end.utcoffset()

