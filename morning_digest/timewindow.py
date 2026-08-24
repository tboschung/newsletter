from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def digest_window(as_of: datetime, timezone: ZoneInfo) -> tuple[datetime, datetime]:
    local = as_of.astimezone(timezone)
    today_cutoff = datetime.combine(local.date(), time(6), tzinfo=timezone)
    end = today_cutoff if local >= today_cutoff else today_cutoff - timedelta(days=1)
    start = datetime.combine(end.date() - timedelta(days=1), time(6), tzinfo=timezone)
    return start, end
