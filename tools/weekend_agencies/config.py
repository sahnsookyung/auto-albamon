"""Fixed policy for the short weekend staffing-agency search."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from .agencies import HOTEL_AGENCIES

try:
    from tools.jobscout_config import SETTINGS
except ModuleNotFoundError:  # Direct CLI execution via tools/.
    from jobscout_config import SETTINGS

SEARCH_START = SETTINGS.weekend_agencies.search_start
SEARCH_END = SETTINGS.weekend_agencies.search_end
WEEKENDS_ONLY = SETTINGS.weekend_agencies.weekends_only
MIN_ACTUAL_HOURLY_WON = SETTINGS.weekend_agencies.minimum_actual_hourly_won


def weekend_dates(start: date = SEARCH_START, end: date = SEARCH_END) -> tuple[date, ...]:
    if end < start:
        raise ValueError("search end must not precede search start")
    return tuple(
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
        if (start + timedelta(days=offset)).weekday() >= 5
    )


SEARCH_DATES = (
    weekend_dates()
    if WEEKENDS_ONLY
    else tuple(
        SEARCH_START + timedelta(days=offset)
        for offset in range((SEARCH_END - SEARCH_START).days + 1)
    )
)
OUTPUT_PREFIX = Path("artifacts/weekend-agencies/latest")
CACHE_DIR = Path("artifacts/weekend-agencies/cache")
LOCK_PATH = CACHE_DIR / "refresh.lock"
SOURCE_CACHE_PATH = CACHE_DIR / "source_snapshots.json"
LOCATION_CACHE_PATH = CACHE_DIR / "locations.json"
ROUTE_CACHE_PATH = CACHE_DIR / "routes.json"
SHARED_KAKAO_QUOTA_PATH = Path(
    "artifacts/albamon/cache/kakao_public_transit_quota.json"
)

SOURCE_LABELS = {agency.agency_id: agency.name for agency in HOTEL_AGENCIES}
