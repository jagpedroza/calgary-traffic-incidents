"""Quick date-range presets, evaluated in Calgary local time."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .core import FIRST_DATE, LOCAL_TZ

PRESETS = [
    "Today",
    "Yesterday",
    "Last 7 days",
    "Last 30 days",
    "This month",
    "This year",
    "Last 12 months",
    "All time",
    "Custom",
]
DEFAULT_PRESET = "Today"


def today_local() -> date:
    return datetime.now(LOCAL_TZ).date()


def preset_range(name: str, today: date | None = None) -> tuple[date, date]:
    t = today or today_local()
    match name:
        case "Today":
            return t, t
        case "Yesterday":
            y = t - timedelta(days=1)
            return y, y
        case "Last 7 days":
            return t - timedelta(days=6), t
        case "Last 30 days":
            return t - timedelta(days=29), t
        case "This month":
            return t.replace(day=1), t
        case "This year":
            return t.replace(month=1, day=1), t
        case "Last 12 months":
            return t - timedelta(days=364), t
        case "All time":
            return FIRST_DATE, t
    raise ValueError(f"Unknown preset: {name}")


def grain_for(start: date, end: date) -> str:
    """Pick a sensible time-series bucket for a range."""
    days = (end - start).days + 1
    if days <= 3:
        return "hour"
    if days <= 92:
        return "day"
    if days <= 730:
        return "week"
    return "month"
