#!/usr/bin/env python3
"""Create one deduplicated Albamon report from saved and supplemental lists."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import re
import sys
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from tools.albamon_cache import (
        DEFAULT_APPLIED_REFRESH_HOURS,
        DEFAULT_CLOSING_REFRESH_HOURS,
        DEFAULT_DETAIL_CACHE_PATH,
        DEFAULT_ELIGIBLE_REFRESH_HOURS,
        DEFAULT_EXCLUDED_TTL_DAYS,
        DEFAULT_RETENTION_DAYS,
        DetailCache,
    )
    from tools.albamon_jobs import (
        DEFAULT_MANUAL_REVIEWS_PATH,
        DEFAULT_PAGE_SIZE,
        DEFAULT_SUIT_NO,
        DEFAULT_WORKERS,
        GENERAL_API_URL,
        LATE_NIGHT_AVAILABILITY,
        GeneralApiClient,
        NEXT_DATA_RE,
        SITE_URL,
        AlbamonError,
        enum_description,
        escape_markdown,
        fetch_all_search_jobs,
        fetch_records,
        fetch_saved_condition,
        load_manual_reviews,
        parse_clock_minutes,
        read_url,
        shift_fits_availability,
    )
    from tools.albamon_runtime import atomic_text_writer, atomic_write_text
    from tools.jobscout_config import SETTINGS
except ModuleNotFoundError:  # Support direct execution from the repository root.
    from albamon_cache import (
        DEFAULT_APPLIED_REFRESH_HOURS,
        DEFAULT_CLOSING_REFRESH_HOURS,
        DEFAULT_DETAIL_CACHE_PATH,
        DEFAULT_ELIGIBLE_REFRESH_HOURS,
        DEFAULT_EXCLUDED_TTL_DAYS,
        DEFAULT_RETENTION_DAYS,
        DetailCache,
    )
    from albamon_jobs import (
        DEFAULT_MANUAL_REVIEWS_PATH,
        DEFAULT_PAGE_SIZE,
        DEFAULT_SUIT_NO,
        DEFAULT_WORKERS,
        GENERAL_API_URL,
        LATE_NIGHT_AVAILABILITY,
        GeneralApiClient,
        NEXT_DATA_RE,
        SITE_URL,
        AlbamonError,
        enum_description,
        escape_markdown,
        fetch_all_search_jobs,
        fetch_records,
        fetch_saved_condition,
        load_manual_reviews,
        parse_clock_minutes,
        read_url,
        shift_fits_availability,
    )
    from albamon_runtime import atomic_text_writer, atomic_write_text
    from jobscout_config import SETTINGS


SUMMER_URL = (
    "https://www.albamon.com/jobs/guerrilla/summer-campaign"
    "?guerrillaBoothNo=35"
)
URGENT_URL = "https://www.albamon.com/jobs/urgent"
REMOTE_URL = "https://www.albamon.com/jobs/telecommuting"
SUBWAY_URL = "https://www.albamon.com/jobs/subway"
DEFAULT_APPLIED_HISTORY_PATH = Path("tools/albamon_applied_jobs.json")
DEFAULT_SPECIAL_SOURCE_CACHE_PATH = Path(
    "artifacts/albamon/cache/special_sources.json"
)
DEFAULT_SPECIAL_SOURCE_CACHE_HOURS = SETTINGS.cache.special_source_hours
SPECIAL_SOURCE_CACHE_VERSION = 3
_SPECIAL_SOURCE_OPTIONS = ("summer", "urgent", "remote", "station_safety")
SPECIAL_SOURCE_NAMES = tuple(
    source for source in _SPECIAL_SOURCE_OPTIONS
    if source in SETTINGS.albamon.enabled_sources
)
DEFAULT_AVAILABILITY_END_DATE = SETTINGS.albamon.availability_end_date
TITLE_SINGLE_DAY_SIGNAL_RE = re.compile(
    r"(?:당일\s*(?:알바|근무|업무|스태프|직원|모집)|"
    r"(?:하루|1\s*일)\s*(?:만|단기|알바|근무|업무|스태프|가능)|"
    r"단\s*하루|일일\s*(?:알바|근무|스태프))",
    re.IGNORECASE,
)
SEOUL_DISTRICTS = (
    "강남구", "강동구", "강북구", "강서구", "관악구", "광진구", "구로구",
    "금천구", "노원구", "도봉구", "동대문구", "동작구", "마포구", "서대문구",
    "서초구", "성동구", "성북구", "송파구", "양천구", "영등포구", "용산구",
    "은평구", "종로구", "중구", "중랑구",
)
TARGET_DISTRICTS = SETTINGS.albamon.target_districts
SEOUL_ALL_AREAS = [
    {"si": "I000", "gu": "", "dong": "", "name": "서울 전체"}
]
_EVENING_AREA_CATALOG = {
    "강남구": {"si": "I000", "gu": "I010", "dong": "", "name": "강남구 전체"},
    "서초구": {"si": "I000", "gu": "I150", "dong": "", "name": "서초구 전체"},
    "송파구": {"si": "I000", "gu": "I180", "dong": "", "name": "송파구 전체"},
}
EVENING_CORRIDOR_AREAS = [
    copy.deepcopy(_EVENING_AREA_CATALOG[district])
    for district in SETTINGS.albamon.evening.districts
]
EVENING_CORRIDOR_DISTRICTS = set(SETTINGS.albamon.evening.districts)
EVENING_CORRIDOR_STATIONS = SETTINGS.albamon.evening.stations
TARGET_SUBWAY_STATIONS = tuple(
    {
        "areaCode": station.area_code,
        "subwayLineCode": station.line_code,
        "stationCode": station.station_code,
        "name": station.name.removesuffix("역"),
        "latitude": station.latitude,
        "longitude": station.longitude,
    }
    for station in SETTINGS.albamon.evening.station_definitions
)
EVENING_STATION_AREA_LIMIT_MINUTES = (
    SETTINGS.albamon.evening.station_walk_limit_minutes
)
REMOTE_MIN_HOURLY_WON = SETTINGS.albamon.remote_minimum_hourly_won
REMOTE_PRIVACY_OR_SCAM_RE = re.compile(
    r"방\s*사진|사진\s*제출|영상\s*데이터|행동\s*영상|얼굴\s*(?:촬영|사진)|"
    r"보험|회원\s*가입|계정\s*(?:생성|대여)|수수료|선입금|홍보\s*(?:요원|알바)|"
    r"리워드|온라인\s*조사|설문\s*조사"
)
_WORK_PERIOD_CATALOG = {
    "one_day": {"key": "ONE_DAY", "value": "5", "description": "하루(1일)"},
    "under_one_week": {
        "key": "LESS_THAN_ONE_WEEK", "value": "10", "description": "1주일이하"
    },
    "one_week_to_one_month": {
        "key": "ONE_WEEK_TO_ONE_MONTH", "value": "20", "description": "1주일~1개월"
    },
}
REQUESTED_WORK_PERIOD_TYPES = [
    copy.deepcopy(_WORK_PERIOD_CATALOG[name])
    for name in SETTINGS.albamon.work_periods
]
REQUESTED_WORK_PERIOD_LABELS = {
    item["description"] for item in REQUESTED_WORK_PERIOD_TYPES
}
LATE_NIGHT_WORK_PERIOD_TYPES = copy.deepcopy(REQUESTED_WORK_PERIOD_TYPES)
LATE_NIGHT_WORK_PERIOD_LABELS = {
    item["description"] for item in LATE_NIGHT_WORK_PERIOD_TYPES
}
TEMPORARY_SINGLE_DAY_ACTIVE_DATES = set(
    SETTINGS.albamon.temporary_single_day.active_dates
)
TEMPORARY_SINGLE_DAY_TARGET_DATES = set(
    SETTINGS.albamon.temporary_single_day.target_dates
)
TEMPORARY_SINGLE_DAY_LANE = "temporary_single_day"
WORK_PERIOD_MINIMUM_DAYS = {
    "하루(1일)": 1,
    "1주일이하": 1,
    "1주일~1개월": 7,
}
# Ranking weights are user-taste settings, not claims about exact transit time.
DISTRICT_PROXIMITY_PENALTIES = dict(SETTINGS.ranking.district_penalties)
COMBINED_TIER_A_MINIMUM, COMBINED_TIER_B_MINIMUM, COMBINED_TIER_C_MINIMUM = (
    SETTINGS.ranking.combined_tier_minimums
)
SOURCE_LABELS = {
    "suit": "맞춤알바",
    "multi_slot": "1일 선택근무",
    "late_night": "·".join(SETTINGS.albamon.evening.districts) + " 역세권 저녁",
    "summer": "여름알바",
    "urgent": "급구알바",
    "remote": "재택알바",
    "station_safety": f"{len(EVENING_CORRIDOR_STATIONS)}개 설정역 안전망",
}
SOURCE_ORDER = tuple(
    "late_night" if source == "evening" else source
    for source in SETTINGS.albamon.enabled_sources
    if source != "evening" or SETTINGS.albamon.evening.enabled
)
ADULT_RESTRICTED_TEXT = "성인인증이 필요한 공고"
CLOSED_POSTING_ERROR_CODES = {"RCRV005", "RCRV006"}
CLINICAL_STUDY_REVIEW_REASON = (
    "clinical-study dates and medical eligibility are study-specific, not verified "
    "as Saturday-and-Sunday-only"
)
UTILITY_MODEL = {
    "purpose": "Prioritize practical applications using the configured schedule and commute policy",
    "neutral_factors": [
        "Shift end time, including before/after 17:00 and overnight work",
    ],
    "tier_thresholds": {
        "A": COMBINED_TIER_A_MINIMUM,
        "B": COMBINED_TIER_B_MINIMUM,
        "C": COMBINED_TIER_C_MINIMUM,
        "D": 0,
    },
    "important_factors": [
        "verified weekend schedule",
        "verified short commitment",
        "advertised pay",
        "configured district fit",
        "hard-requirement burden",
        "application friction",
        "summer cold-work usefulness for B-Mart",
        "actual-center uncertainty for Coupang",
    ],
    "district_proximity_penalties": DISTRICT_PROXIMITY_PENALTIES,
    "confirmed_applicant_facts": [],
}


def fetch_page_condition(url: str) -> dict[str, Any]:
    """Read the initial search condition embedded in an Albamon list page."""

    document = read_url(url, expect_json=False)
    match = NEXT_DATA_RE.search(document)
    if not match:
        raise AlbamonError(f"Search page did not contain structured data: {url}")
    try:
        page_data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise AlbamonError(f"Search page contained invalid structured data: {url}") from exc
    condition = page_data.get("props", {}).get("pageProps", {}).get("condition")
    if not isinstance(condition, dict):
        raise AlbamonError(f"Search page did not contain a condition: {url}")
    return condition


def apply_saved_preferences(
    source_condition: dict[str, Any], saved_condition: dict[str, Any]
) -> dict[str, Any]:
    """Apply the user's saved filter fields while preserving source-list type."""

    filtered = copy.deepcopy(source_condition)
    filtered["condition"] = copy.deepcopy(saved_condition["condition"])
    filtered["condition"]["areas"] = copy.deepcopy(SEOUL_ALL_AREAS)
    # Albamon can mutate a saved condition independently of this workflow. The
    # The availability horizon is shorter than a month, so do not spend detail
    # or route requests on Albamon's one-to-three-month bucket. The remaining
    # broad buckets are still verified against the posting's actual commitment.
    filtered["condition"]["workPeriodTypes"] = copy.deepcopy(
        REQUESTED_WORK_PERIOD_TYPES
    )
    # The user explicitly wants Albamon's "협의포함" behavior at collection time.
    filtered["condition"]["excludeNegoWorkWeek"] = False
    # Adult/bar jobs are outside the requested search and previously accounted
    # for almost a third of all detail downloads.
    filtered["condition"]["excludeBar"] = True
    return filtered


def build_multi_slot_condition(saved_condition: dict[str, Any]) -> dict[str, Any]:
    """Collect one-day jobs before Albamon's single-day field hides slot pools.

    Employers can publish a one-day posting with one representative weekday in
    structured metadata while the detailed description offers many selectable
    dates. The normal saved search applies the weekend field on Albamon's
    server, so those postings never reach our detail-level schedule verifier.
    This bounded supplemental query removes only that weekday constraint and
    limits the result set to one-day work.
    """

    filtered = apply_saved_preferences(saved_condition, saved_condition)
    condition = filtered["condition"]
    condition["workPeriodTypes"] = [
        copy.deepcopy(_WORK_PERIOD_CATALOG["one_day"])
    ]
    condition["workWeekTypes"] = []
    condition["workDayTypes"] = []
    condition["excludeNegoWorkWeek"] = False
    return filtered


def build_late_night_condition(saved_condition: dict[str, Any]) -> dict[str, Any]:
    """Collect corridor jobs for the configured evening availability lane.

    Weekday constraints are removed, but the source is limited to Albamon's
    one-day through one-month buckets. Exact shift containment, the rolling
    availability deadline, and any contradictory longer commitment are verified
    from the detail page.
    """

    filtered = apply_saved_preferences(saved_condition, saved_condition)
    condition = filtered["condition"]
    condition["areas"] = copy.deepcopy(EVENING_CORRIDOR_AREAS)
    condition["workPeriodTypes"] = copy.deepcopy(LATE_NIGHT_WORK_PERIOD_TYPES)
    condition["workWeekTypes"] = []
    condition["workDayTypes"] = []
    condition["excludeNegoWorkWeek"] = False
    return filtered


def build_remote_condition(
    source_condition: dict[str, Any], saved_condition: dict[str, Any]
) -> dict[str, Any]:
    """Collect remote jobs independently of Albamon's Seoul-area metadata."""

    filtered = apply_saved_preferences(source_condition, saved_condition)
    condition = filtered["condition"]
    condition["areas"] = []
    condition["workWeekTypes"] = []
    condition["workDayTypes"] = []
    condition["includeHomeWork"] = True
    condition["excludeNegoWorkWeek"] = False
    return filtered


def build_station_safety_condition(
    source_condition: dict[str, Any], saved_condition: dict[str, Any]
) -> dict[str, Any]:
    """Collect short-term listings tagged to a configured preferred station."""

    filtered = apply_saved_preferences(source_condition, saved_condition)
    condition = filtered["condition"]
    # AREA_SUBWAY ignores district-style filtering. Explicit station codes keep
    # this safety net bounded and detail-level checks enforce the real worksite.
    condition["areas"] = []
    condition["workWeekTypes"] = []
    condition["workDayTypes"] = []
    condition["excludeNegoWorkWeek"] = False
    extension = filtered.setdefault("extensionCondition", {})
    extension["subway"] = {
        "subways": [
            {
                key: station[key]
                for key in ("areaCode", "subwayLineCode", "stationCode", "name")
            }
            for station in TARGET_SUBWAY_STATIONS
        ]
    }
    return filtered


def _special_source_fingerprint(saved: dict[str, Any]) -> str:
    serialized = json.dumps(
        saved.get("condition") or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_special_source_cache(
    path: Path | None,
    saved: dict[str, Any],
    *,
    max_age_hours: int,
    now: datetime | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        checked_at = datetime.fromisoformat(str(payload.get("checked_at") or ""))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if checked_at.tzinfo is None:
        checked_at = checked_at.astimezone()
    observed_at = now or datetime.now().astimezone()
    if (
        payload.get("version") != SPECIAL_SOURCE_CACHE_VERSION
        or payload.get("saved_condition_fingerprint")
        != _special_source_fingerprint(saved)
        or observed_at - checked_at > timedelta(hours=max_age_hours)
    ):
        return None
    sources = payload.get("sources")
    if not isinstance(sources, dict):
        return None
    jobs: dict[str, list[dict[str, Any]]] = {}
    totals: dict[str, int] = {}
    for source in SPECIAL_SOURCE_NAMES:
        entry = sources.get(source)
        if not isinstance(entry, dict) or not isinstance(entry.get("jobs"), list):
            return None
        jobs[source] = entry["jobs"]
        totals[source] = int(entry.get("total") or len(entry["jobs"]))
    return jobs, totals


def write_special_source_cache(
    path: Path | None,
    saved: dict[str, Any],
    source_jobs: dict[str, list[dict[str, Any]]],
    source_totals: dict[str, int],
) -> None:
    if path is None:
        return
    payload = {
        "version": SPECIAL_SOURCE_CACHE_VERSION,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "saved_condition_fingerprint": _special_source_fingerprint(saved),
        "sources": {
            source: {
                "total": source_totals[source],
                "jobs": source_jobs[source],
            }
            for source in SPECIAL_SOURCE_NAMES
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
    )


def collect_source_jobs(
    *,
    suit_no: int,
    page_size: int,
    performance_metrics: dict[str, Any] | None = None,
    special_source_cache: Path | None = DEFAULT_SPECIAL_SOURCE_CACHE_PATH,
    special_source_cache_hours: int = DEFAULT_SPECIAL_SOURCE_CACHE_HOURS,
    include_late_night: bool = True,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, int]]:
    started = time.perf_counter()
    if suit_no <= 0:
        raise ValueError(
            "Albamon saved search is not configured. Copy "
            "config/jobscout.local.example.toml to config/jobscout.local.toml "
            "and set albamon.saved_search_id before refreshing."
        )
    saved = fetch_saved_condition(suit_no)
    cached_special = load_special_source_cache(
        special_source_cache,
        saved,
        max_age_hours=special_source_cache_hours,
    )
    conditions: dict[str, dict[str, Any]] = {}
    if "suit" in SOURCE_ORDER:
        conditions["suit"] = apply_saved_preferences(saved, saved)
    if "multi_slot" in SOURCE_ORDER:
        conditions["multi_slot"] = build_multi_slot_condition(saved)
    if include_late_night and "late_night" in SOURCE_ORDER:
        conditions["late_night"] = build_late_night_condition(saved)
    source_jobs: dict[str, list[dict[str, Any]]] = (
        dict(cached_special[0]) if cached_special else {}
    )
    source_totals: dict[str, int] = (
        dict(cached_special[1]) if cached_special else {}
    )
    if cached_special is None and SPECIAL_SOURCE_NAMES:
        source_urls = {
            "summer": SUMMER_URL,
            "urgent": URGENT_URL,
            "remote": REMOTE_URL,
            "station_safety": SUBWAY_URL,
        }
        with ThreadPoolExecutor(max_workers=len(SPECIAL_SOURCE_NAMES)) as executor:
            condition_futures = {
                source: executor.submit(fetch_page_condition, source_urls[source])
                for source in SPECIAL_SOURCE_NAMES
            }
            for source, future in condition_futures.items():
                source_condition = future.result()
                if source == "remote":
                    conditions[source] = build_remote_condition(
                        source_condition, saved
                    )
                elif source == "station_safety":
                    conditions[source] = build_station_safety_condition(
                        source_condition, saved
                    )
                else:
                    conditions[source] = apply_saved_preferences(
                        source_condition, saved
                    )
    with GeneralApiClient() as api_client:
        if conditions:
            executor_workers = min(len(conditions), SETTINGS.albamon.workers)
        else:
            executor_workers = 1
        with ThreadPoolExecutor(max_workers=executor_workers) as executor:
            futures = {
                executor.submit(
                    fetch_all_search_jobs,
                    condition,
                    page_size=page_size,
                    strict_weekdays=False,
                    api_client=api_client,
                ): source
                for source, condition in conditions.items()
            }
            for future in as_completed(futures):
                source = futures[future]
                jobs, total = future.result()
                source_jobs[source] = jobs
                source_totals[source] = total
        cache_write_errors = 0
        if cached_special is None and SPECIAL_SOURCE_NAMES:
            try:
                write_special_source_cache(
                    special_source_cache,
                    saved,
                    source_jobs,
                    source_totals,
                )
            except OSError:
                cache_write_errors = 1
        if performance_metrics is not None:
            performance_metrics.update(
                {
                    "list_api_requests": api_client.requests,
                    "list_api_retries": api_client.retries,
                    "list_api_bytes_received": api_client.bytes_received,
                    "list_collection_seconds": round(
                        time.perf_counter() - started, 3
                    ),
                    "special_source_cache_hits": (
                        len(SPECIAL_SOURCE_NAMES) if cached_special else 0
                    ),
                    "special_source_network_sources": (
                        0 if cached_special else len(SPECIAL_SOURCE_NAMES)
                    ),
                    "special_source_cache_write_errors": cache_write_errors,
                }
            )
    return saved, source_jobs, source_totals


def merge_by_recruit_no(
    source_jobs: dict[str, list[dict[str, Any]]]
) -> tuple[list[dict[str, Any]], dict[int, list[str]], int]:
    jobs_by_id: dict[int, dict[str, Any]] = {}
    sources_by_id: dict[int, set[str]] = defaultdict(set)
    raw_count = 0
    # Prefer the freshly fetched saved-search row when a cached special-source
    # row describes the same posting. Special sources still contribute labels.
    source_order = [
        source
        for source in SOURCE_ORDER
        if source in source_jobs
    ]
    source_order.extend(
        source for source in source_jobs if source not in source_order
    )
    for source in source_order:
        jobs = source_jobs[source]
        raw_count += len(jobs)
        for job in jobs:
            recruit_no = job.get("recruitNo")
            if not isinstance(recruit_no, int):
                continue
            jobs_by_id.setdefault(recruit_no, job)
            sources_by_id[recruit_no].add(source)
    source_map = {
        recruit_no: sorted(sources) for recruit_no, sources in sources_by_id.items()
    }
    return list(jobs_by_id.values()), source_map, raw_count - len(jobs_by_id)


def source_parity_summary(
    source_map: dict[int, list[str]], *, sample_limit: int = 100
) -> dict[str, Any]:
    """Expose exclusive discovery so source regressions stay visible."""

    exclusive: dict[str, list[int]] = {source: [] for source in SOURCE_ORDER}
    multi_source_count = 0
    for recruit_no, sources in source_map.items():
        if len(sources) == 1:
            exclusive.setdefault(sources[0], []).append(recruit_no)
        elif len(sources) > 1:
            multi_source_count += 1
    return {
        "multi_source_count": multi_source_count,
        "exclusive_counts": {
            source: len(recruit_nos)
            for source, recruit_nos in exclusive.items()
            if source in SOURCE_ORDER
        },
        "exclusive_recruit_nos": {
            source: sorted(recruit_nos, reverse=True)[:sample_limit]
            for source, recruit_nos in exclusive.items()
            if source in SOURCE_ORDER
        },
        "exclusive_sample_limit": sample_limit,
        "exclusive_samples_truncated": {
            source: max(0, len(recruit_nos) - sample_limit)
            for source, recruit_nos in exclusive.items()
            if source in SOURCE_ORDER
        },
    }


SEOUL_COORDINATE_BOUNDS = {
    "min_latitude": 37.40,
    "max_latitude": 37.72,
    "min_longitude": 126.73,
    "max_longitude": 127.28,
}
OUTSIDE_SEOUL_ADDRESS_RE = re.compile(
    r"(?:인천|부산|대구|광주|대전|울산|세종)(?:광역시)?|"
    r"경기(?:도)?|"
    r"(?:강원|충북|충남|전북|전남|경북|경남|제주)(?:특별자치)?도"
)


def _list_salary_amount(job: dict[str, Any]) -> int:
    digits = re.sub(r"\D", "", str(job.get("pay") or ""))
    return int(digits) if digits else 0


def _list_shift_fits_evening(job: dict[str, Any]) -> bool:
    text = " ".join(
        str(job.get(field) or "")
        for field in ("workingTime", "recruitTitle")
    )
    for match in re.finditer(
        r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)\s*(?:~|～|-)\s*"
        r"([01]?\d|2[0-3]):([0-5]\d)(?!\d)",
        text,
    ):
        start = f"{int(match.group(1)):02d}:{match.group(2)}"
        end = f"{int(match.group(3)):02d}:{match.group(4)}"
        if shift_fits_availability(start, end):
            return True
    return False


def station_safety_pre_detail_exclusion_reason(
    job: dict[str, Any],
) -> str | None:
    """Keep only list rows likely to fit weekend/evening availability."""

    text = " ".join(
        str(job.get(field) or "")
        for field in (
            "recruitTitle",
            "workingWeek",
            "workingDate",
            "workingPeriod",
            "workingTime",
        )
    )
    if re.search(
        r"주말|토요일|일요일|\([토일]\)|"
        r"(?<![0-9가-힣])(?:토|일)(?![가-힣])",
        text,
    ):
        return None
    if _list_shift_fits_evening(job):
        return None
    selectability_text = " ".join(
        str(job.get(field) or "")
        for field in ("recruitTitle", "workingWeek", "workingDate", "workingTime")
    )
    if re.search(r"하루|1일", str(job.get("workingPeriod") or "")) and re.search(
        r"원하는\s*날|(?:날짜|요일|근무일)\s*선택|"
        r"(?:근무일|요일|시간)\s*협의",
        selectability_text,
    ):
        return None
    return (
        "station safety-net listing has no list-level weekend, evening, "
        "or selectable-day signal"
    )


def remote_pre_detail_exclusion_reason(job: dict[str, Any]) -> str | None:
    """Bound remote detail reads to transparent hourly, low-risk listings."""

    text = " ".join(
        (
            str(job.get("recruitTitle") or ""),
            " ".join(str(part) for part in job.get("parts") or []),
        )
    )
    if REMOTE_PRIVACY_OR_SCAM_RE.search(text):
        return "remote listing requests risky data, signup, fees, or promotional work"
    salary_type = enum_description(job.get("payType"))
    if salary_type != "시급":
        return "remote safety lane requires a stated hourly wage"
    amount = _list_salary_amount(job)
    if amount < REMOTE_MIN_HOURLY_WON:
        return (
            "remote hourly wage is below the configured floor: "
            f"{amount:,} < {REMOTE_MIN_HOURLY_WON:,} won"
        )
    return None


def pre_detail_exclusion_reason(
    job: dict[str, Any], *, allow_outside_seoul: bool = False
) -> str | None:
    """Apply only exclusions that are conclusive in list-level metadata."""

    title = str(job.get("recruitTitle") or "")
    parts = " ".join(str(part) for part in job.get("parts") or [])
    if re.search(
        r"생동성|임상\s*시험|건강기능식품(?:\s*(?:시험|테스트))?|"
        r"의료기기(?:\s*(?:시험|테스트))?|화장품[^\n]{0,20}(?:시험|테스트)|"
        r"필러|임플란트",
        f"{title} {parts}",
    ):
        return "clinical-study recruitment is outside the weekend job workflow"
    if re.search(
        r"운전면허\s*(?:필수|소지)|발렛|탁송|대리운전|"
        r"라이더|배달\s*대행|(?:배송|운전)\s*(?:기사|사원|직원|알바)",
        title,
    ):
        return "title conclusively identifies a driving/licence role"

    address = str(job.get("workplaceAddress") or "").strip()
    latitude = job.get("latitude")
    longitude = job.get("longitude")
    coordinates_outside = bool(
        isinstance(latitude, (int, float))
        and isinstance(longitude, (int, float))
        and not (
            SEOUL_COORDINATE_BOUNDS["min_latitude"]
            <= float(latitude)
            <= SEOUL_COORDINATE_BOUNDS["max_latitude"]
            and SEOUL_COORDINATE_BOUNDS["min_longitude"]
            <= float(longitude)
            <= SEOUL_COORDINATE_BOUNDS["max_longitude"]
        )
    )
    if (
        not allow_outside_seoul
        and
        address
        and "서울" not in address
        and OUTSIDE_SEOUL_ADDRESS_RE.search(address)
        and coordinates_outside
    ):
        return "verified worksite address and coordinates are outside Seoul"
    return None


def prefilter_search_jobs(
    jobs: list[dict[str, Any]],
    source_map: dict[int, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for job in jobs:
        recruit_no = int(job["recruitNo"])
        sources = set((source_map or {}).get(recruit_no, []))
        remote_candidate = "remote" in sources
        remote_only = bool(sources) and sources <= {"remote"}
        station_only = bool(sources) and sources <= {"station_safety"}
        reason = pre_detail_exclusion_reason(
            job, allow_outside_seoul=remote_only
        )
        if reason is None and remote_candidate:
            reason = remote_pre_detail_exclusion_reason(job)
        if reason is None and station_only:
            reason = station_safety_pre_detail_exclusion_reason(job)
        if reason is None:
            kept.append(job)
            continue
        excluded.append(
            {
                "recruit_no": recruit_no,
                "url": f"{SITE_URL}/jobs/detail/{recruit_no}",
                "title": str(job.get("recruitTitle") or ""),
                "company": str(job.get("companyName") or ""),
                "area": str(job.get("workplaceArea") or ""),
                "working_address": str(job.get("workplaceAddress") or ""),
                "exclusion_reason": reason,
            }
        )
    return kept, excluded


def list_duplicate_key(job: dict[str, Any]) -> str:
    if not str(job.get("recruitTitle") or "").strip() or not str(
        job.get("companyName") or ""
    ).strip():
        return f"recruit:{int(job['recruitNo'])}"
    application_types = sorted(
        enum_description(value) for value in job.get("applicationTypes") or []
    )
    parts = sorted(str(value) for value in job.get("parts") or [])
    values = (
        job.get("recruitTitle"),
        job.get("companyName"),
        job.get("workplaceAddress"),
        job.get("workingTime"),
        job.get("workingPeriod"),
        job.get("workingWeek"),
        enum_description(job.get("payType")),
        job.get("pay"),
        parts,
        application_types,
    )
    return "|".join(normalized_text(value) for value in values)


def deduplicate_search_jobs(
    jobs: list[dict[str, Any]],
    source_map: dict[int, list[str]],
) -> tuple[list[dict[str, Any]], int]:
    """Collapse only list records with an exact operational signature."""

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        groups[list_duplicate_key(job)].append(job)
    result: list[dict[str, Any]] = []
    removed = 0
    for group in groups.values():
        group.sort(key=lambda item: int(item["recruitNo"]), reverse=True)
        representative = group[0]
        duplicate_ids = [int(item["recruitNo"]) for item in group[1:]]
        representative["_pre_detail_duplicate_recruit_nos"] = duplicate_ids
        representative["_pre_detail_duplicate_urls"] = [
            f"{SITE_URL}/jobs/detail/{recruit_no}" for recruit_no in duplicate_ids
        ]
        representative_id = int(representative["recruitNo"])
        source_map[representative_id] = sorted(
            {
                source
                for item in group
                for source in source_map.get(int(item["recruitNo"]), [])
            }
        )
        removed += len(duplicate_ids)
        result.append(representative)
    result.sort(key=lambda item: int(item["recruitNo"]), reverse=True)
    return result, removed


def target_district(record: dict[str, Any]) -> str | None:
    title = str(record.get("title") or "")
    title_specific = False
    explicit_title_districts = {
        candidate for candidate in SEOUL_DISTRICTS if candidate in title
    }
    stem_title_districts = {
        candidate
        for candidate in SEOUL_DISTRICTS
        if len(candidate.removesuffix("구")) >= 2
        and candidate.removesuffix("구") in title
    }
    title_districts = explicit_title_districts or stem_title_districts
    if re.search(r"수도권\s*전\s*지역|서울\s*전\s*지역|전국\s*매장", title):
        record["area_screen_note"] = "work location is broader than a verified Seoul district"
        return None
    haystack = f"{record.get('area', '')} {record.get('working_address', '')}"
    try:
        latitude = float(record.get("latitude"))
        longitude = float(record.get("longitude"))
    except (TypeError, ValueError):
        coordinates_outside_seoul = False
    else:
        coordinates_outside_seoul = not (
            SEOUL_COORDINATE_BOUNDS["min_latitude"]
            <= latitude
            <= SEOUL_COORDINATE_BOUNDS["max_latitude"]
            and SEOUL_COORDINATE_BOUNDS["min_longitude"]
            <= longitude
            <= SEOUL_COORDINATE_BOUNDS["max_longitude"]
        )
    if (
        coordinates_outside_seoul
        and not re.search(r"서울(?:특별시)?", haystack)
        and not explicit_title_districts
    ):
        record["area_screen_note"] = (
            "coordinates are outside Seoul and the address has no explicit Seoul city"
        )
        return None
    explicitly_outside_seoul = bool(
        re.search(
            r"(?:인천|부산|대구|광주|대전|울산|세종)(?:광역시)?|"
            r"(?:경기|강원|충청|전라|경상|제주)(?:특별자치)?도",
            haystack,
        )
        and not re.search(r"서울(?:특별시)?", haystack)
    )
    address_district = None if explicitly_outside_seoul else next(
        (candidate for candidate in TARGET_DISTRICTS if candidate in haystack), None
    )
    address_looks_administrative = bool(re.search(r"본사|사무실", haystack))
    # Albamon usually provides a precise work address. Prefer it to a bare
    # district stem embedded in a branch name (for example, 신세계강남 is in
    # 서초구). An explicit "...구" in a non-logistics title may identify an
    # event worksite whose saved address is only the recruiting office.
    if (
        address_district is not None
        and not address_looks_administrative
        and not (
            len(explicit_title_districts) == 1
            and not record.get("logistics_job")
        )
    ):
        district = address_district
        if record.get("logistics_category") == "Coupang Logistics Services":
            record["area_screen_note"] = (
                f"listed pickup/landing area includes {district}; actual logistics center "
                "must be confirmed before applying"
            )
        else:
            record["area_screen_note"] = f"detail address is in {district}"
        return district
    if len(title_districts) == 1:
        district = next(iter(title_districts))
        title_specific = True
        record["area_screen_note"] = f"posting title identifies work in {district}"
    elif len(title_districts) > 1:
        if address_district in title_districts:
            district = address_district
        else:
            record["area_screen_note"] = (
                "posting title lists multiple Seoul districts without one verified branch"
            )
            return None
    else:
        district = address_district
    if district is None:
        record["area_screen_note"] = "work address is outside Seoul or not district-specific"
        return None
    if record.get("logistics_category") == "Coupang Logistics Services":
        record["area_screen_note"] = (
            f"listed pickup/landing area includes {district}; actual logistics center "
            "must be confirmed before applying"
        )
        return district
    if title_specific:
        return district
    record["area_screen_note"] = f"detail address is in {district}"
    return district


def station_safety_proximity(record: dict[str, Any]) -> dict[str, Any]:
    """Return the conservative walking estimate to the closest target station."""

    try:
        latitude = float(record.get("latitude"))
        longitude = float(record.get("longitude"))
    except (TypeError, ValueError):
        return {
            "eligible": False,
            "nearest_station": "",
            "straight_line_km": None,
            "walk_minutes": None,
            "basis": "worksite coordinates unavailable",
        }

    def distance_km(station: dict[str, Any]) -> float:
        earth_radius_km = 6371.0088
        lat1 = math.radians(latitude)
        lat2 = math.radians(float(station["latitude"]))
        delta_lat = lat2 - lat1
        delta_lon = math.radians(float(station["longitude"]) - longitude)
        value = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
        )
        return earth_radius_km * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))

    station, straight_line_km = min(
        ((station, distance_km(station)) for station in TARGET_SUBWAY_STATIONS),
        key=lambda item: item[1],
    )
    walk_minutes = straight_line_km * 1.2 / 4.8 * 60
    eligible = walk_minutes <= EVENING_STATION_AREA_LIMIT_MINUTES
    return {
        "eligible": eligible,
        "nearest_station": f"{station['name']}역",
        "straight_line_km": round(straight_line_km, 3),
        "walk_minutes": round(walk_minutes, 1),
        "basis": (
            f"{station['name']}역 conservative walk estimate {walk_minutes:.1f} min "
            "(straight line × 1.2 at 4.8 km/h)"
        ),
    }


def work_period_is_requested(record: dict[str, Any]) -> bool:
    """Reject only an explicit period outside the remaining short-term buckets."""

    work_period = str(record.get("work_period") or "").strip()
    return not work_period or work_period in REQUESTED_WORK_PERIOD_LABELS


def _iso_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def minimum_commitment_days(record: dict[str, Any]) -> int:
    """Return the safest verified/bucket lower bound for the commitment."""

    candidates = [
        WORK_PERIOD_MINIMUM_DAYS.get(str(record.get("work_period") or ""), 0)
    ]
    explicit_days = record.get("minimum_commitment_days")
    if isinstance(explicit_days, (int, float)) and explicit_days > 0:
        candidates.append(math.ceil(float(explicit_days)))
    minimum_months = record.get("minimum_commitment_months")
    if isinstance(minimum_months, (int, float)) and minimum_months > 0:
        candidates.append(math.ceil(float(minimum_months) * 30))
    return max(candidates)


def availability_window_exclusion_reasons(
    record: dict[str, Any],
    *,
    as_of: date,
    availability_end: date,
) -> list[str]:
    """Reject work that cannot finish inside the configured availability window."""

    if as_of > availability_end:
        return [f"availability window ended on {availability_end.isoformat()}"]

    reasons: list[str] = []
    work_start = _iso_date(record.get("work_term_start"))
    if work_start and work_start > availability_end:
        reasons.append(
            f"work term starts after availability ends: {work_start.isoformat()}"
        )

    effective_end = effective_work_term_end(record, as_of=as_of)
    work_end = _iso_date(effective_end)
    if work_end and work_end > availability_end:
        reasons.append(
            f"work term extends beyond {availability_end.isoformat()}: "
            f"{work_end.isoformat()}"
        )

    minimum_days = minimum_commitment_days(record)
    earliest_start = max(as_of, work_start) if work_start else as_of
    if (
        minimum_days
        and earliest_start + timedelta(days=minimum_days - 1) > availability_end
    ):
        reasons.append(
            f"minimum {minimum_days}-day commitment cannot finish by "
            f"{availability_end.isoformat()}"
        )
    return reasons


def availability_window_basis(
    record: dict[str, Any],
    *,
    as_of: date,
    availability_end: date,
) -> str:
    effective_end = _iso_date(effective_work_term_end(record, as_of=as_of))
    if effective_end:
        return (
            f"verified work term ends {effective_end.isoformat()}, no later than "
            f"{availability_end.isoformat()}"
        )
    minimum_days = minimum_commitment_days(record)
    if minimum_days:
        return (
            f"minimum {minimum_days}-day commitment fits by "
            f"{availability_end.isoformat()}"
        )
    return (
        f"no required work beyond {availability_end.isoformat()} found; "
        "confirm the final work date before applying"
    )


def combined_exclusion_reasons(
    record: dict[str, Any], *, as_of: date, availability_end: date
) -> list[str]:
    reasons: list[str] = []
    if not record["target_district"]:
        reasons.append(record.get("area_screen_note") or "outside Seoul")
    if not record["weekend_compatible"]:
        reasons.append(record["weekend_compatibility_basis"])
    if not record["demographic_compatible"]:
        reasons.append(record["demographic_compatibility_basis"])
    if not record.get("education_compatible", True):
        reasons.append(record["education_compatibility_basis"])
    if record["work_term_ended"]:
        reasons.append(
            record.get("work_term_expiry_basis")
            or f"work term ends by {record['work_term_end']}"
        )
    reasons.extend(
        availability_window_exclusion_reasons(
            record, as_of=as_of, availability_end=availability_end
        )
    )
    if record["driving_license_required"]:
        reasons.append(record["driving_license_basis"])
    work_period = str(record.get("work_period") or "").strip()
    if not work_period_is_requested(record):
        reasons.append(f"work period is outside requested buckets: {work_period}")
    if record["requires_month_or_longer"]:
        reasons.append(record["duration_requirement"])
    elif record.get("duration_review_status") == "non_numeric_requirement":
        reasons.append(record["duration_requirement"])
    if record.get("clinical_study"):
        reasons.append(CLINICAL_STUDY_REVIEW_REASON)
    return reasons


def late_night_exclusion_reasons(
    record: dict[str, Any],
    *,
    as_of: date | None = None,
    availability_end: date = DEFAULT_AVAILABILITY_END_DATE,
) -> list[str]:
    """Apply hard constraints for the short-term station-corridor evening lane."""

    reasons: list[str] = []
    if record.get("target_district") not in EVENING_CORRIDOR_DISTRICTS:
        reasons.append(
            "verified worksite is outside the configured evening corridor"
        )
    if not record.get("late_night_compatible"):
        reasons.append(
            f"no advertised shift fits fully within {LATE_NIGHT_AVAILABILITY}"
        )
    work_period = str(record.get("work_period") or "").strip()
    if work_period not in LATE_NIGHT_WORK_PERIOD_LABELS:
        reasons.append(
            "work period is not verified in Albamon's one-day through "
            f"one-month buckets: {work_period or 'unknown'}"
        )
    if record.get("requires_month_or_longer"):
        reasons.append(record["duration_requirement"])
    elif record.get("duration_review_status") in {
        "non_numeric_requirement",
        "unverified",
    }:
        reasons.append(record["duration_requirement"])
    if not record["demographic_compatible"]:
        reasons.append(record["demographic_compatibility_basis"])
    if not record.get("education_compatible", True):
        reasons.append(record["education_compatibility_basis"])
    if record["work_term_ended"]:
        reasons.append(
            record.get("work_term_expiry_basis")
            or f"work term ends by {record['work_term_end']}"
        )
    reasons.extend(
        availability_window_exclusion_reasons(
            record,
            as_of=as_of or datetime.now().astimezone().date(),
            availability_end=availability_end,
        )
    )
    if record["driving_license_required"]:
        reasons.append(record["driving_license_basis"])
    if record.get("clinical_study"):
        reasons.append(CLINICAL_STUDY_REVIEW_REASON)
    return reasons


def remote_exclusion_reasons(
    record: dict[str, Any], *, as_of: date, availability_end: date
) -> list[str]:
    """Fail closed for short-term remote work with comparable hourly pay."""

    reasons: list[str] = []
    text = " ".join(
        (
            str(record.get("title") or ""),
            str(record.get("company") or ""),
            " ".join(str(item) for item in record.get("hard_requirements") or []),
        )
    )
    if REMOTE_PRIVACY_OR_SCAM_RE.search(text):
        reasons.append(
            "remote listing requests risky data, signup, fees, or promotional work"
        )
    if record.get("application_risk_signals"):
        reasons.append(
            "remote detail contains an unsafe application signal: "
            + ", ".join(str(item) for item in record["application_risk_signals"])
        )
    if record.get("salary_type") != "시급":
        reasons.append("remote safety lane requires a stated hourly wage")
    elif numeric_pay(record) < REMOTE_MIN_HOURLY_WON:
        reasons.append(
            "remote hourly wage is below the configured floor: "
            f"{numeric_pay(record):,} < {REMOTE_MIN_HOURLY_WON:,} won"
        )
    if not record.get("demographic_compatible"):
        reasons.append(record.get("demographic_compatibility_basis") or "demographic mismatch")
    if not record.get("education_compatible", True):
        reasons.append(record.get("education_compatibility_basis") or "education mismatch")
    if record.get("application_possible") is False:
        reasons.append("Albamon currently marks the posting as not applicable")
    if record.get("work_term_ended"):
        reasons.append(
            record.get("work_term_expiry_basis")
            or f"work term ends by {record.get('work_term_end')}"
        )
    reasons.extend(
        availability_window_exclusion_reasons(
            record, as_of=as_of, availability_end=availability_end
        )
    )
    if record.get("driving_license_required"):
        reasons.append(record.get("driving_license_basis") or "driving licence required")
    if not work_period_is_requested(record):
        reasons.append(
            "work period is outside requested buckets: "
            f"{record.get('work_period') or 'unknown'}"
        )
    if record.get("requires_month_or_longer"):
        reasons.append(record.get("duration_requirement") or "one-month minimum")
    elif record.get("duration_review_status") in {
        "non_numeric_requirement",
        "unverified",
    }:
        reasons.append(record.get("duration_requirement") or "duration unverified")
    if record.get("clinical_study"):
        reasons.append(CLINICAL_STUDY_REVIEW_REASON)
    if record.get("hard_requirement_review_status") == (
        "unverified: no readable description text"
    ):
        reasons.append("remote job description could not be verified")
    schedule_is_flexible = bool(
        re.search(r"협의|자율|원하는\s*(?:날|시간)", str(record.get("work_days") or ""))
        and re.search(r"협의|자율|원하는\s*시간", str(record.get("work_time") or ""))
    )
    if not (
        record.get("weekend_compatible")
        or record.get("late_night_compatible")
        or schedule_is_flexible
    ):
        reasons.append(
            "remote schedule is not verified as weekend, "
            f"{LATE_NIGHT_AVAILABILITY}, or fully flexible"
        )
    return reasons


def temporary_single_day_exclusion_reasons(
    record: dict[str, Any], *, as_of: date
) -> list[str]:
    """Apply hard filters to the configured self-expiring one-day lane."""

    if not TEMPORARY_SINGLE_DAY_ACTIVE_DATES:
        return ["temporary one-day search is disabled in configuration"]
    if as_of not in TEMPORARY_SINGLE_DAY_ACTIVE_DATES:
        active_dates = ", ".join(
            sorted(value.isoformat() for value in TEMPORARY_SINGLE_DAY_ACTIVE_DATES)
        )
        return [f"temporary one-day search is active only on {active_dates}"]
    target_values = {
        target.isoformat() for target in TEMPORARY_SINGLE_DAY_TARGET_DATES
    }
    options = set(record.get("target_date_options") or []) & target_values
    if not options:
        targets = ", ".join(sorted(target_values)) or "a configured target date"
        return [f"no independently selectable target date verified ({targets})"]

    reasons: list[str] = []
    if not record.get("target_district"):
        reasons.append(record.get("area_screen_note") or "outside Seoul")
    work_period = str(record.get("work_period") or "").strip()
    work_period_value = str(record.get("work_period_value") or "").strip()
    if work_period != "하루(1일)" and work_period_value != "5":
        reasons.append(f"work period is not verified as one day: {work_period or 'unknown'}")
    if not record.get("demographic_compatible"):
        reasons.append(record.get("demographic_compatibility_basis") or "demographic mismatch")
    if not record.get("education_compatible", True):
        reasons.append(record.get("education_compatibility_basis") or "education mismatch")
    if record.get("work_term_ended"):
        reasons.append(
            record.get("work_term_expiry_basis")
            or f"work term ends by {record.get('work_term_end')}"
        )
    if record.get("driving_license_required"):
        reasons.append(record.get("driving_license_basis") or "driving licence required")
    if record.get("requires_month_or_longer"):
        reasons.append(record.get("duration_requirement") or "one-month minimum")
    elif record.get("duration_review_status") == "non_numeric_requirement":
        reasons.append(record.get("duration_requirement") or "long-term requirement")
    if record.get("clinical_study"):
        reasons.append(CLINICAL_STUDY_REVIEW_REASON)
    return reasons


def normalized_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())


def is_clinical_study(record: dict[str, Any]) -> bool:
    title = str(record.get("title") or "")
    parts = " ".join(str(item) for item in record.get("parts", []))
    return bool(
        "생동성·임상시험" in parts
        or re.search(r"건강기능식품|의료기기|임상\s*시험|생동성", title)
    )


def enrich_special_requirements(record: dict[str, Any]) -> None:
    """Add evidence from manually reviewed, shared campaign templates."""

    record["clinical_study"] = is_clinical_study(record)
    if record["clinical_study"]:
        for requirement in (
            "Healthy adult who passes study-specific medical eligibility screening",
            "Must accept the study-specific visit schedule and disclosed health risks",
        ):
            if requirement not in record["hard_requirements"]:
                record["hard_requirements"].append(requirement)
        record["hard_requirement_review_status"] = (
            "campaign images manually reviewed; exact study eligibility must be checked"
        )

    if record.get("logistics_category") == "Baemin B-Mart micro-fulfilment":
        for requirement in (
            "At least age 19",
            "Able to handle refrigerated/frozen products and low-temperature work",
            "Able to lift and handle stock repeatedly",
            "Wages must be paid to an account in the applicant's own name",
        ):
            if requirement not in record["hard_requirements"]:
                record["hard_requirements"].append(requirement)
        record["hard_requirement_review_status"] = (
            "shared B-Mart campaign image manually reviewed"
        )


def semantic_duplicate_key(
    record: dict[str, Any], *, include_material_terms: bool = True
) -> str:
    if record.get("is_coupang") and re.search(r"소분|분류", record.get("title", "")):
        fields: list[Any] = [
            "coupang-cls-daily-sorting",
            record.get("logistics_category"),
            record.get("work_period"),
        ]
        if include_material_terms:
            fields.extend(
                (
                    record.get("work_days"),
                    record.get("work_time"),
                    record.get("salary_type"),
                    record.get("salary"),
                    record.get("weekend_shift_options"),
                    record.get("late_night_shift_options"),
                )
            )
        return "|".join(normalized_text(value) for value in fields)
    if (
        "왓어브레드" in str(record.get("title") or "")
        and "잠실롯데월드몰" in str(record.get("title") or "")
    ):
        return "what-a-bread-jamsil-weekend-part-timer"
    if "팥고당 정미소 사당점" in str(record.get("title") or ""):
        return "|".join(
            (
                "patgodang-sadang-weekend-part-timer",
                normalized_text(record.get("company")),
                normalized_text(record.get("working_address")),
                normalized_text(record.get("work_time")),
                normalized_text(record.get("salary")),
            )
        )
    if record.get("clinical_study"):
        return "clinical-study|" + "|".join(
            (
                normalized_text(record.get("title")),
                normalized_text(record.get("company")),
            )
        )
    fields = [
        "title",
        "company",
        "working_address",
        "work_time",
    ]
    if include_material_terms:
        fields.extend(("work_days", "salary_type", "salary"))
    return "|".join(normalized_text(record.get(field)) for field in fields)


def deduplicate_semantic(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[semantic_duplicate_key(record)].append(record)

    deduplicated: list[dict[str, Any]] = []
    removed = 0
    for group in groups.values():
        group.sort(key=lambda item: item["recruit_no"], reverse=True)
        representative = group[0]
        duplicate_recruit_nos = {
            int(recruit_no)
            for item in group
            for recruit_no in item.get("duplicate_recruit_nos") or []
        }
        duplicate_recruit_nos.update(item["recruit_no"] for item in group[1:])
        duplicate_recruit_nos.discard(int(representative["recruit_no"]))
        representative["duplicate_recruit_nos"] = sorted(
            duplicate_recruit_nos, reverse=True
        )
        duplicate_urls = {
            str(url)
            for item in group
            for url in item.get("duplicate_urls") or []
        }
        duplicate_urls.update(item["url"] for item in group[1:])
        representative["duplicate_urls"] = sorted(duplicate_urls)
        representative["listed_areas"] = sorted(
            {str(item.get("area") or "") for item in group if item.get("area")}
        )
        representative["source_pages"] = sorted(
            {
                source
                for item in group
                for source in item.get("source_pages", [])
            }
        )
        representative["duplicate_listing_count"] = 1 + len(
            representative["duplicate_recruit_nos"]
        )
        removed += len(group) - 1
        deduplicated.append(representative)
    deduplicated.sort(key=lambda item: item["recruit_no"], reverse=True)
    return deduplicated, removed


def before_threshold_label(record: dict[str, Any]) -> str:
    result = record.get("ends_before_threshold")
    if record.get("has_daytime_shift_ending_before_threshold") and result is not True:
        return "yes, at least one advertised weekend option"
    if result is None:
        return "unknown/negotiable"
    if result and record.get("overnight"):
        return "yes, but at clock time next day"
    return "yes" if result else "no"


def early_shift_label(record: dict[str, Any]) -> str:
    evidence = record.get("early_shift_evidence") or []
    if evidence:
        return str(evidence[0]).split(" — ", 1)[0]
    return str(record.get("work_time") or "time not shown")


def numeric_pay(record: dict[str, Any]) -> int:
    digits = re.sub(r"\D", "", str(record.get("salary") or ""))
    return int(digits) if digits else 0


def assess_utility(record: dict[str, Any]) -> tuple[int, str, list[str], list[str]]:
    """Score practical utility using only verified report metadata."""

    score = 50
    reasons: list[str] = []
    cautions: list[str] = []

    def adjust(points: int, label: str, *, caution: bool = False) -> None:
        nonlocal score
        score += points
        rendered = f"{points:+d} {label}"
        (cautions if caution else reasons).append(rendered)

    district = str(record.get("target_district") or "")
    if district in DISTRICT_PROXIMITY_PENALTIES:
        proximity_penalty = DISTRICT_PROXIMITY_PENALTIES[district]
        record["district_proximity_penalty"] = proximity_penalty
        record["district_proximity_basis"] = (
            "configured Seoul district fit; "
            "not exact transit time"
        )
        if proximity_penalty:
            adjust(
                proximity_penalty,
                f"{district} has a configured fit penalty",
                caution=True,
            )
        else:
            adjust(0, f"{district} is a core preferred district")

    eligibility_lanes = set(record.get("eligibility_lanes") or [])
    weekend_basis = str(record.get("weekend_compatibility_basis") or "")
    if (
        "songpa_late_night" in eligibility_lanes
        and "weekend_short_term" not in eligibility_lanes
    ):
        adjust(
            10,
            f"advertised shift fits the station-corridor {LATE_NIGHT_AVAILABILITY} availability",
        )
    elif (
        TEMPORARY_SINGLE_DAY_LANE in eligibility_lanes
        and "weekend_short_term" not in eligibility_lanes
    ):
        adjust(10, "configured one-day target option is detail-verified")
    elif weekend_basis.startswith("weekend") or weekend_basis == "explicit weekdays":
        adjust(10, "weekend schedule is explicit")
    else:
        adjust(3, "posting explicitly offers a negotiable weekend option")

    duration_status = record.get("duration_review_status")
    if duration_status == "verified_shorter":
        adjust(14, "short commitment fits the current availability window")
    elif duration_status == "not_found":
        adjust(6, "no conflicting minimum commitment found")
    elif duration_status == "not_stated":
        adjust(-6, "numeric commitment is not stated", caution=True)
    elif duration_status == "negotiable":
        adjust(-3, "duration remains negotiable", caution=True)
    elif duration_status == "non_numeric_preference":
        adjust(-8, "long-term availability is preferred", caution=True)

    pay = numeric_pay(record)
    salary_type = record.get("salary_type")
    if salary_type == "시급":
        if pay >= 15_000:
            adjust(15, f"high hourly pay ({pay:,} won)")
        elif pay >= 13_500:
            adjust(11, f"above-average hourly pay ({pay:,} won)")
        elif pay >= 12_500:
            adjust(7, f"solid hourly pay ({pay:,} won)")
        elif pay >= 12_000:
            adjust(4, f"hourly pay ({pay:,} won)")
        elif pay >= 11_000:
            adjust(1, f"hourly pay ({pay:,} won)")
    elif salary_type == "일급":
        if pay >= 130_000:
            adjust(10, f"high daily pay ({pay:,} won)")
        elif pay >= 100_000:
            adjust(6, f"daily pay ({pay:,} won)")
        elif pay >= 90_000:
            adjust(3, f"daily pay ({pay:,} won)")

    requirements = record.get("hard_requirements") or []
    if not requirements:
        adjust(7, "no explicit hard requirement found")
    for requirement in requirements:
        normalized = requirement.lower()
        if "health certificate" in normalized:
            adjust(2, "health certificate requirement is confirmed met")
        elif "experience" in normalized:
            adjust(-10, "relevant experience required", caution=True)
        elif "chinese" in normalized or "english" in normalized:
            adjust(-16, "language proficiency required", caution=True)
        elif "178 cm" in normalized:
            adjust(-10, "height requirement", caution=True)
        elif "refrigerated/frozen" in normalized:
            adjust(7, "cold-work option is useful in summer")
        elif "lift and handle stock" in normalized:
            adjust(-5, "repetitive physical stock handling", caution=True)
        elif any(
            marker in normalized
            for marker in ("outfit", "dress shoes", "tattoo", "piercing")
        ):
            adjust(-2, "appearance or clothing requirement", caution=True)
        elif "foreign applicants" in normalized:
            adjust(-5, "visa/work-hour condition may require verification", caution=True)
        elif any(
            marker in normalized
            for marker in ("at least age", "must be at least", "minors", "own name")
        ):
            continue
        else:
            adjust(-5, f"hard requirement: {requirement}", caution=True)

    methods = set(record.get("application_methods") or [])
    if "homepage" in methods and not ({"online", "easy_sms", "sms"} & methods):
        adjust(-5, "external homepage application", caution=True)
    elif {"online", "easy_sms", "sms"} & methods:
        adjust(4, "low-friction Albamon/quick application")
        if len(methods) >= 3:
            adjust(2, "multiple application channels")

    if record.get("logistics_category") == "Coupang Logistics Services":
        adjust(-12, "actual logistics center is not verified from pickup-area ad", caution=True)
        if record.get("duplicate_listing_count", 1) >= 20:
            adjust(-5, "high-volume repeated campaign", caution=True)

    if score >= COMBINED_TIER_A_MINIMUM:
        tier = "A"
    elif score >= COMBINED_TIER_B_MINIMUM:
        tier = "B"
    elif score >= COMBINED_TIER_C_MINIMUM:
        tier = "C"
    else:
        tier = "D"
    return score, tier, reasons, cautions


def prioritize_records(records: list[dict[str, Any]]) -> None:
    for record in records:
        score, tier, reasons, cautions = assess_utility(record)
        record["utility_score"] = score
        record["utility_tier"] = tier
        record["utility_reasons"] = reasons
        record["utility_cautions"] = cautions
    records.sort(
        key=lambda item: (item["utility_score"], item["recruit_no"]),
        reverse=True,
    )
    for rank, record in enumerate(records, start=1):
        record["utility_rank"] = rank


def work_term_ends_on_or_before(work_term_end: Any, *, as_of: date) -> bool:
    """Return whether a posting's final work date is no later than the cutoff."""

    if not isinstance(work_term_end, str) or not work_term_end:
        return False
    try:
        return date.fromisoformat(work_term_end) <= as_of
    except ValueError:
        return False


def exact_shift_start_minutes(record: dict[str, Any]) -> list[int]:
    """Return advertised shift starts without treating unrelated times as shifts."""

    values: list[Any] = [
        record.get("work_time"),
        record.get("start_time"),
        *(record.get("weekend_shift_options") or []),
        *(record.get("late_night_shift_options") or []),
        *(record.get("target_date_option_evidence") or []),
    ]
    starts: set[int] = set()
    for value in values:
        text = str(value or "")
        direct = parse_clock_minutes(text)
        if direct is not None:
            starts.add(direct)
        for match in re.finditer(
            r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)\s*(?:~|～|-)", text
        ):
            starts.add(int(match.group(1)) * 60 + int(match.group(2)))
    return sorted(starts)


def record_work_term_has_expired(
    record: dict[str, Any], *, observed_at: date | datetime
) -> tuple[bool, str]:
    """Expire past dates, or today's posting only after its last exact shift starts."""

    observed_date = observed_at.date() if isinstance(observed_at, datetime) else observed_at
    effective_end = _iso_date(effective_work_term_end(record, as_of=observed_date))
    if effective_end is None:
        return False, "no exact final work date"
    if effective_end < observed_date:
        return True, f"final work date {effective_end.isoformat()} is in the past"
    if effective_end > observed_date:
        return False, f"final work date is {effective_end.isoformat()}"
    if not isinstance(observed_at, datetime):
        return False, "final work date is today; current time was not supplied"
    shift_starts = exact_shift_start_minutes(record)
    if not shift_starts:
        return False, "final work date is today; exact shift start is unavailable"
    current_minutes = observed_at.hour * 60 + observed_at.minute
    last_start = max(shift_starts)
    expired = current_minutes >= last_start
    return (
        expired,
        "final work date is today; last advertised shift starts at "
        f"{last_start // 60:02d}:{last_start % 60:02d}",
    )


def effective_work_term_end(record: dict[str, Any], *, as_of: date) -> str | None:
    """Return the safest final date from structured fields and explicit title dates.

    A materially older title range is not rescued by a contradictory future
    structured date. Those mixed postings are unsafe to surface because the
    applicant-facing title can describe a schedule that has already ended.
    """

    structured_end: str | None = None
    structured = record.get("work_term_end")
    if isinstance(structured, str) and structured:
        try:
            date.fromisoformat(structured)
        except ValueError:
            pass
        else:
            structured_end = structured

    title = str(record.get("title") or "")
    title_match = re.search(
        r"(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})\s*[~～-]\s*"
        r"(?:(\d{1,2})\s*/\s*)?(\d{1,2})\s*일?",
        title,
    )
    if title_match:
        start_month = int(title_match.group(1))
        end_month = int(title_match.group(3) or start_month)
        end_day = int(title_match.group(4))
    else:
        korean_range = re.search(
            r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일?\s*[~～-]\s*"
            r"(?:(\d{1,2})\s*월\s*)?(\d{1,2})\s*일",
            title,
        )
        if korean_range:
            start_month = int(korean_range.group(1))
            end_month = int(korean_range.group(3) or start_month)
            end_day = int(korean_range.group(4))
        else:
            korean_date_list = re.search(
                r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일?\s*[,·]\s*"
                r"(\d{1,2})\s*일",
                title,
            )
            if korean_date_list:
                start_month = int(korean_date_list.group(1))
                end_month = start_month
                end_day = int(korean_date_list.group(3))
            elif (
                str(record.get("work_period") or "") == "하루(1일)"
                or TITLE_SINGLE_DAY_SIGNAL_RE.search(title)
            ):
                korean_single = re.search(
                    r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일",
                    title,
                )
                slash_single = re.search(
                    r"(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})(?!\d|\s*[~～-])",
                    title,
                )
                single = korean_single or slash_single
                if not single:
                    return structured_end
                start_month = int(single.group(1))
                end_month = start_month
                end_day = int(single.group(2))
            else:
                return structured_end
    end_year = as_of.year + (1 if end_month < start_month else 0)
    try:
        title_end = date(end_year, end_month, end_day).isoformat()
    except ValueError:
        return structured_end
    if structured_end is None:
        return title_end
    return min(structured_end, title_end)


def prune_expired_audit_records(
    records: list[dict[str, Any]], *, observed_at: date | datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Remove conclusively expired jobs from a latest-report audit section."""

    kept: list[dict[str, Any]] = []
    expired_records: list[dict[str, Any]] = []
    for record in records:
        expired, expiry_basis = record_work_term_has_expired(
            record, observed_at=observed_at
        )
        if not expired:
            kept.append(record)
            continue
        record["work_term_ended"] = True
        record["work_term_expiry_basis"] = expiry_basis
        expired_records.append(record)
    return kept, expired_records


def exclude_report_jobs_ending_on_or_before(
    report: dict[str, Any],
    *,
    as_of: date | datetime,
    availability_end: date | None = None,
) -> dict[str, Any]:
    """Apply stale-date and optional availability cutoffs to an existing report."""

    as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of
    removed: list[dict[str, Any]] = []
    for section in ("jobs", "logistics_jobs"):
        kept: list[dict[str, Any]] = []
        for record in report.get(section) or []:
            effective_end = effective_work_term_end(record, as_of=as_of_date)
            removal_reasons: list[str] = []
            expired, expiry_basis = record_work_term_has_expired(
                record, observed_at=as_of
            )
            record["work_term_expiry_basis"] = expiry_basis
            if expired:
                if not record.get("work_term_end"):
                    record["work_term_end"] = effective_end
                    record["work_term_end_source"] = "posting title date range"
                record["work_term_ended"] = True
                removal_reasons.append(expiry_basis)
            if availability_end is not None:
                availability_reasons = availability_window_exclusion_reasons(
                    record, as_of=as_of_date, availability_end=availability_end
                )
                removal_reasons.extend(availability_reasons)
                record["availability_window_start_date"] = as_of_date.isoformat()
                record["availability_end_date"] = availability_end.isoformat()
                record["availability_window_days"] = max(
                    0, (availability_end - as_of_date).days + 1
                )
                record["availability_compatible"] = not availability_reasons
                record["availability_compatibility_basis"] = (
                    availability_reasons[0]
                    if availability_reasons
                    else availability_window_basis(
                        record, as_of=as_of_date, availability_end=availability_end
                    )
                )
                if not work_period_is_requested(record):
                    removal_reasons.append(
                        "work period is outside requested buckets: "
                        f"{record.get('work_period') or 'unknown'}"
                    )
            if not removal_reasons:
                kept.append(record)
                continue
            reasons = record.setdefault("exclusion_reasons", [])
            for reason in removal_reasons:
                if reason not in reasons:
                    reasons.append(reason)
            removed.append(record)
        report[section] = kept
        prioritize_records(kept)

    excluded_jobs, previously_expired = prune_expired_audit_records(
        list(report.get("excluded_jobs") or []), observed_at=as_of
    )
    pre_detail_jobs, expired_pre_detail = prune_expired_audit_records(
        list(report.get("pre_detail_excluded_jobs") or []), observed_at=as_of
    )
    report["excluded_jobs"] = excluded_jobs
    report["pre_detail_excluded_jobs"] = pre_detail_jobs
    excluded_ids = {int(record["recruit_no"]) for record in excluded_jobs}
    excluded_jobs.extend(
        record
        for record in removed
        if not record.get("work_term_ended")
        and int(record["recruit_no"]) not in excluded_ids
    )
    newly_expired = [record for record in removed if record.get("work_term_ended")]
    report["expired_jobs_flushed_count"] = int(
        report.get("expired_jobs_flushed_count") or 0
    ) + len(previously_expired) + len(expired_pre_detail) + len(newly_expired)
    report["work_term_cutoff_date"] = as_of_date.isoformat()
    if availability_end is not None:
        report["availability_window_start_date"] = as_of_date.isoformat()
        report["availability_end_date"] = availability_end.isoformat()
        report["availability_window_days"] = max(
            0, (availability_end - as_of_date).days + 1
        )
    report["eligible_non_logistics_count"] = len(report.get("jobs") or [])
    report["eligible_logistics_group_count"] = len(
        report.get("logistics_jobs") or []
    )
    report["eligible_count"] = (
        report["eligible_non_logistics_count"]
        + report["eligible_logistics_group_count"]
    )
    report["eligible_late_night_count"] = sum(
        "songpa_late_night" in (record.get("eligibility_lanes") or [])
        for record in [
            *(report.get("jobs") or []),
            *(report.get("logistics_jobs") or []),
        ]
    )
    report["eligible_remote_count"] = sum(
        "remote_short_term" in (record.get("eligibility_lanes") or [])
        for record in [
            *(report.get("jobs") or []),
            *(report.get("logistics_jobs") or []),
        ]
    )
    report["excluded_count"] = len(excluded_jobs) + len(pre_detail_jobs)
    report["exclusion_reason_counts"] = dict(
        Counter(
            reason
            for record in excluded_jobs
            for reason in (record.get("exclusion_reasons") or [])
        ).most_common()
    )
    return report


def load_applied_history(path: Path) -> dict[str, Any]:
    """Load a privacy-minimized authenticated application-history snapshot."""

    if not path.exists():
        return {
            "checked_at": "",
            "source": "authenticated application history not available",
            "history_counts": {},
            "jobs": {},
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), dict):
        raise ValueError(f"Applied-history file has an invalid shape: {path}")
    return payload


# Albamon sometimes closes a listing and republishes the same employer/title/
# location under a fresh recruit number. Keep reviewed aliases explicit so a
# reopened post is not accidentally submitted twice.
HISTORICAL_REPOST_ALIASES: dict[int, tuple[int, ...]] = {
    118270862: (118158513,),
}


def annotate_report_with_application_history(
    report: dict[str, Any], history: dict[str, Any]
) -> dict[str, Any]:
    """Mark eligible records that match authenticated Albamon history.

    Semantic deduplication can replace a recruit ID with a newer representative,
    so alternate recruit IDs are checked as well as the representative ID.
    """

    history_jobs = history.get("jobs") or {}
    matched_history_ids: set[str] = set()
    eligible_records = [
        *(report.get("jobs") or []),
        *(report.get("logistics_jobs") or []),
    ]
    for record in eligible_records:
        candidate_ids = [record.get("recruit_no")]
        candidate_ids.extend(record.get("duplicate_recruit_nos") or [])
        candidate_ids.extend(
            HISTORICAL_REPOST_ALIASES.get(int(record.get("recruit_no") or 0), ())
        )
        matched_id = next(
            (
                str(candidate_id)
                for candidate_id in candidate_ids
                if str(candidate_id) in history_jobs
            ),
            None,
        )
        application = history_jobs.get(matched_id, {}) if matched_id else {}
        record["already_applied"] = bool(matched_id)
        record["applied_recruit_no"] = matched_id or ""
        record["applied_at"] = application.get("applied_at") or ""
        record["applied_method"] = application.get("method") or ""
        record["application_read_status"] = application.get("read_status") or ""
        record["application_history_section"] = (
            application.get("history_section") or ""
        )
        if matched_id:
            matched_history_ids.add(matched_id)

    already_applied = sum(
        1 for record in eligible_records if record["already_applied"]
    )
    report["application_history_checked_at"] = history.get("checked_at") or ""
    report["application_history_source"] = history.get("source") or ""
    report["application_history_counts"] = history.get("history_counts") or {}
    report["already_applied_count"] = already_applied
    report["not_yet_applied_count"] = len(eligible_records) - already_applied
    report["matched_application_history_recruit_nos"] = sorted(
        matched_history_ids, key=int
    )
    return report


def job_notes(record: dict[str, Any]) -> list[str]:
    notes = [
        f"Weekend basis: {record['weekend_compatibility_basis']}",
        f"Duration: {record['duration_requirement']}",
    ]
    if record.get("duration_evidence"):
        notes.append(f"Duration evidence: {record['duration_evidence']}")
    if record.get("early_shift_evidence"):
        notes.append(
            "Early weekend option evidence: " + record["early_shift_evidence"][0]
        )
    if record.get("hard_requirements"):
        notes.append("Hard requirements: " + "; ".join(record["hard_requirements"]))
    else:
        notes.append("No explicit hard requirement found")
    if record.get("certificate_requirements"):
        notes.append(
            "Certificate/licence: " + "; ".join(record["certificate_requirements"])
        )
    else:
        notes.append("No explicit certificate or licence requirement found")
    if record.get("preferences"):
        notes.append("Preferences only: " + "; ".join(record["preferences"]))
    if record.get("logistics_job"):
        notes.append(record["cold_storage_status"])
    if record.get("clinical_study"):
        notes.append(
            "Paid clinical-study participation, not an ordinary shift job; exact "
            "medical exclusions, visit dates, compensation, and possible adverse "
            "effects vary by study and must be checked before applying"
        )
    return notes


def search_value(value: Any) -> str:
    if isinstance(value, dict):
        return enum_description(value)
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value or "")


def build_restricted_bar_jobs(
    detail_errors: list[dict[str, Any]],
    jobs_by_id: dict[int, dict[str, Any]],
    source_map: dict[int, list[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    restricted: list[dict[str, Any]] = []
    other_errors: list[dict[str, Any]] = []
    for error in detail_errors:
        if ADULT_RESTRICTED_TEXT not in error["error"]:
            other_errors.append(error)
            continue
        job = jobs_by_id.get(error["recruit_no"], {})
        title = str(job.get("recruitTitle") or "")
        working_week = search_value(job.get("workingWeek"))
        period = search_value(job.get("workingPeriod"))
        weekend_hint = bool(
            re.search(r"주말|토\s*[,/·]\s*일|토일", f"{title} {working_week}")
        )
        restricted.append(
            {
                "recruit_no": error["recruit_no"],
                "url": error["url"],
                "title": title,
                "company": str(job.get("companyName") or ""),
                "area": search_value(
                    job.get("workplaceArea") or job.get("area") or job.get("areas")
                ),
                "working_period": period,
                "working_week": working_week,
                "working_time": search_value(job.get("workingTime")),
                "parts": search_value(job.get("parts")),
                "source_pages": source_map.get(error["recruit_no"], []),
                "weekend_hint": weekend_hint,
                "verification_status": (
                    "held out: adult detail page could not be read through the API"
                ),
            }
        )
    return restricted, other_errors


def split_closed_detail_errors(
    detail_errors: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate Albamon's authoritative closed-posting responses from failures."""

    closed: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []
    for error in detail_errors:
        destination = (
            closed
            if any(
                code in str(error.get("error") or "")
                for code in CLOSED_POSTING_ERROR_CODES
            )
            else unexpected
        )
        destination.append(error)
    return closed, unexpected


def deduplicate_restricted_weekend_hints(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if not record["weekend_hint"]:
            continue
        key = "|".join(
            (
                normalized_text(record["title"]),
                normalized_text(record["company"]),
            )
        )
        groups[key].append(record)

    result: list[dict[str, Any]] = []
    for group in groups.values():
        group.sort(key=lambda item: item["recruit_no"], reverse=True)
        representative = group[0].copy()
        representative["duplicate_recruit_nos"] = [
            item["recruit_no"] for item in group[1:]
        ]
        representative["duplicate_listing_count"] = len(group)
        representative["source_pages"] = sorted(
            {source for item in group for source in item["source_pages"]}
        )
        result.append(representative)
    result.sort(key=lambda item: item["recruit_no"], reverse=True)
    return result


def write_combined_markdown(path: Path, report: dict[str, Any]) -> None:
    normal_jobs = report["jobs"]
    logistics_jobs = report["logistics_jobs"]
    actionable_normal_jobs = [
        item for item in normal_jobs if not item.get("already_applied")
    ]
    actionable_logistics_jobs = [
        item for item in logistics_jobs if not item.get("already_applied")
    ]
    already_applied_jobs = [
        item
        for item in normal_jobs + logistics_jobs
        if item.get("already_applied")
    ]
    actionable_jobs = actionable_normal_jobs + actionable_logistics_jobs
    late_night_jobs = [
        item
        for item in actionable_jobs
        if "songpa_late_night" in (item.get("eligibility_lanes") or [])
    ]
    temporary_single_day_jobs = [
        item
        for item in actionable_jobs
        if TEMPORARY_SINGLE_DAY_LANE in (item.get("eligibility_lanes") or [])
    ]
    remote_jobs = [
        item
        for item in actionable_jobs
        if "remote_short_term" in (item.get("eligibility_lanes") or [])
    ]
    early_daytime = [
        item
        for item in actionable_jobs
        if item["has_daytime_shift_ending_before_threshold"]
    ]
    overnight = [
        item
        for item in actionable_jobs
        if item["ends_before_threshold"] is True and item.get("overnight")
    ]
    lines = [
        "# Albamon combined 맞춤 · 역세권 · 재택 · 여름 · 급구 report",
        "",
        f"Generated: {report['generated_at']}",
        "Target districts: " + ", ".join(report.get("target_districts") or []),
        "Weekend rule: weekend-only (Saturday and/or Sunday), including negotiable schedules only when the posting verifies a weekend option.",
        (
            "Availability rule: work must fit from "
            f"{report.get('availability_window_start_date')} through "
            f"{report.get('availability_end_date')} inclusive. Fixed work terms "
            "ending later and minimum commitments that cannot fit are excluded."
        ),
        "Duration collection: "
        + ", ".join(
            item["description"] for item in REQUESTED_WORK_PERIOD_TYPES
        )
        + ".",
        (
            "Station-corridor evening lane: weekday is unrestricted, the same "
            "availability deadline applies, and a detail-verified shift must fit "
            f"fully within {LATE_NIGHT_AVAILABILITY}. Final eligibility requires "
            f"the worksite to fall within the conservative {EVENING_STATION_AREA_LIMIT_MINUTES}-minute "
            "walking-radius estimate of a configured station; the origin commute "
            "is checked separately."
        ),
        (
            "Temporary one-day lane: active dates "
            + (", ".join(sorted(value.isoformat() for value in TEMPORARY_SINGLE_DAY_ACTIVE_DATES)) or "none")
            + "; independently selectable targets "
            + (", ".join(sorted(value.isoformat() for value in TEMPORARY_SINGLE_DAY_TARGET_DATES)) or "none")
            + "."
        ),
        f"Work-term cutoff: jobs ending before {report.get('work_term_cutoff_date') or report['generated_at'][:10]} are excluded; same-day jobs remain until their last exact advertised shift starts.",
        (
            f"Demographic metadata: age {SETTINGS.albamon.requested_age}, "
            f"gender {SETTINGS.albamon.requested_gender}; education remains a "
            "posting requirement rather than an inferred applicant fact."
        ),
        f"Source results after API filters: {report['source_counts_text']}",
        f"Unique recruit IDs inspected: {report['unique_candidate_count']}",
        f"Eligible non-logistics jobs: {len(normal_jobs)}",
        f"Eligible logistics groups: {len(logistics_jobs)}",
        f"Eligible station-corridor evening candidates before commute screening: {report.get('eligible_late_night_count', len(late_night_jobs))}",
        f"Eligible short-term remote jobs: {report.get('eligible_remote_count', len(remote_jobs))}",
        f"Eligible configured one-day target jobs: {report.get('eligible_temporary_single_day_count', len(temporary_single_day_jobs))}",
        f"Not yet applied: {len(actionable_jobs)}",
        f"Already applied: {len(already_applied_jobs)}",
        f"Authenticated application history checked: {report.get('application_history_checked_at') or 'not checked'}",
        f"Duplicate listings removed: {report['duplicate_listings_removed']}",
        "Adult/bar listings are excluded by the list API before detail retrieval.",
        f"Closed/stale search candidates rejected by the detail API: {report.get('closed_detail_listing_count', 0)}",
        f"Detail-page errors: {len(report['detail_errors'])}",
        f"Detail candidates after safe early screening/dedup: {report.get('detail_candidate_count', report['unique_candidate_count'])}",
        f"Detail cache hits: {(report.get('performance') or {}).get('detail_cache_hits', 0)}",
        f"Detail network jobs: {(report.get('performance') or {}).get('detail_network_jobs', report['unique_candidate_count'])}",
        "Source verification: enabled safety-net tabs are collected with the same "
        "configured short-term preferences where applicable. Detail screening "
        "remains authoritative because negotiated schedules and advertised "
        "locations are broad.",
        f"Source parity: {(report.get('source_parity') or {}).get('multi_source_count', 0)} postings appeared in multiple source tabs; exclusive counts are retained in JSON for regression detection.",
        "Utility ranking: schedule certainty, short commitment, pay, configured "
        "district fit, hard requirements, application friction, summer cold-work "
        "usefulness, and logistics-location certainty. Shift end time is neutral "
        f"except that {LATE_NIGHT_AVAILABILITY} containment is required for the "
        "station-corridor evening lane. District collection is only a prefilter; "
        "the unified report applies the configured station-area walking estimate "
        "and verifies the origin commute separately.",
        "",
        "## Recommended application order",
        "",
    ]
    for record in actionable_normal_jobs[:12]:
        summary_reason = (
            record["utility_reasons"][0]
            if record["utility_reasons"]
            else "eligible under all hard filters"
        )
        caution = (
            f"; main caution: {record['utility_cautions'][0]}"
            if record["utility_cautions"]
            else ""
        )
        lines.append(
            f"- **#{record['utility_rank']} · Tier {record['utility_tier']} · "
            f"{record['utility_score']} points** — "
            f"[{escape_markdown(record['title'])}]({record['url']}); "
            f"{escape_markdown(record['salary_type'])} {escape_markdown(record['salary'])}; "
            f"{escape_markdown(summary_reason + caution)}"
        )
    lines.extend(
        [
            "",
            f"## Schedule reference — daytime shifts ending before {report['end_time_threshold']} (not used in ranking)",
            "",
        ]
    )
    if early_daytime:
        for record in early_daytime:
            lines.append(
                f"- [{escape_markdown(record['title'])}]({record['url']}) — "
                f"early option {escape_markdown(early_shift_label(record))}; "
                f"{escape_markdown(record['area'])}"
            )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Configured one-day matches — one date can be worked independently",
            "",
            "> This temporary lane runs only on its configured active dates and expires automatically.",
            "",
        ]
    )
    if temporary_single_day_jobs:
        for record in temporary_single_day_jobs:
            lines.append(
                f"- [{escape_markdown(record['title'])}]({record['url']}) — "
                f"{escape_markdown(', '.join(record.get('target_date_options') or []))}; "
                f"{escape_markdown(record['area'])}"
            )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Schedule reference — overnight shifts (not used in ranking)",
            "",
        ]
    )
    if overnight:
        for record in overnight:
            lines.append(
                f"- [{escape_markdown(record['title'])}]({record['url']}) — "
                f"{escape_markdown(record['work_time'])}"
            )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Station-corridor evening candidates — "
            f"{LATE_NIGHT_AVAILABILITY} shift; "
            f"{EVENING_STATION_AREA_LIMIT_MINUTES}-minute station-area estimate "
            "checked in unified report",
            "",
            f"> Weekday is unrestricted, but all required work must finish by {report.get('availability_end_date')}. Confirm transport home using the exact advertised shift.",
            "",
        ]
    )
    if late_night_jobs:
        for record in late_night_jobs:
            lines.append(
                f"- [{escape_markdown(record['title'])}]({record['url']}) — "
                f"{escape_markdown('; '.join(record.get('late_night_shift_options') or []))}; "
                f"{escape_markdown(record['area'])}"
            )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Remote matches — hourly, short-term, and schedule-compatible",
            "",
            "> Remote jobs bypass commute routing but remain subject to pay, duration, description, privacy, and scam-signal checks.",
            "",
        ]
    )
    if remote_jobs:
        for record in remote_jobs:
            lines.append(
                f"- [{escape_markdown(record['title'])}]({record['url']}) — "
                f"{escape_markdown(record['salary_type'])} "
                f"{escape_markdown(record['salary'])}; "
                f"{escape_markdown(record['work_time'])}"
            )
    else:
        lines.append("- None")

    def append_jobs(
        title: str,
        records: list[dict[str, Any]],
        *,
        logistics: bool | None,
        show_application_status: bool = False,
    ) -> None:
        lines.extend(["", f"## {title}", ""])
        if not records:
            lines.append("- None")
            return
        for record in records:
            is_logistics = (
                record.get("report_section") == "logistics"
                if logistics is None
                else logistics
            )
            source_names = ", ".join(SOURCE_LABELS[item] for item in record["source_pages"])
            requirements = "; ".join(record["hard_requirements"]) or "None explicitly found"
            certificates = "; ".join(record["certificate_requirements"]) or "None explicitly found"
            lines.extend(
                [
                    f"### #{record['utility_rank']} · Tier {record['utility_tier']} "
                    f"({record['utility_score']}) · "
                    f"[{escape_markdown(record['title'])}]({record['url']})",
                    "",
                    f"- Utility reasons: {escape_markdown('; '.join(record['utility_reasons']) or 'Eligible under all hard filters')}",
                    f"- Utility cautions: {escape_markdown('; '.join(record['utility_cautions']) or 'None identified from the posting')}",
                    f"- Source: {escape_markdown(source_names)}",
                    f"- Eligibility lanes: {escape_markdown(', '.join(record.get('eligibility_lanes') or []))}",
                    f"- Area: {escape_markdown(record['area'])}",
                    f"- Schedule: {escape_markdown(record['work_week'] or record['work_days'])}; {escape_markdown(record['work_time'])}",
                    f"- Matching night options: {escape_markdown('; '.join(record.get('late_night_shift_options') or []) or 'None')}",
                    f"- Matching configured date options: {escape_markdown(', '.join(record.get('target_date_options') or []) or 'None')}",
                    f"- Pay: {escape_markdown(record['salary_type'])} {escape_markdown(record['salary'])}",
                    f"- Ends before {report['end_time_threshold']}: {before_threshold_label(record)}",
                    f"- Commitment: {escape_markdown(record['duration_requirement'])}",
                    f"- Hard requirements: {escape_markdown(requirements)}",
                    f"- Certificate/licence: {escape_markdown(certificates)}",
                    f"- Application channels: {escape_markdown(', '.join(record['application_methods']))}",
                ]
            )
            if show_application_status:
                application_summary = " · ".join(
                    value
                    for value in (
                        record.get("applied_at") or "date unavailable",
                        record.get("applied_method") or "method unavailable",
                        record.get("application_read_status") or "read status unavailable",
                    )
                    if value
                )
                lines.append(
                    f"- Application status: **Already applied** · {escape_markdown(application_summary)}"
                )
            if is_logistics:
                lines.extend(
                    [
                        f"- Logistics category: {escape_markdown(record['logistics_category'])}",
                        f"- Summer/cold note: {escape_markdown(record['cold_storage_status'])}",
                        f"- Listed/pickup areas: {escape_markdown(', '.join(record['listed_areas']))}",
                    ]
                )
            if record["duplicate_listing_count"] > 1:
                lines.append(
                    f"- Deduplication: {record['duplicate_listing_count']} equivalent listings combined; alternate recruit IDs retained in JSON"
                )
            lines.append("- Notes:")
            for note in job_notes(record):
                lines.append(f"  - {escape_markdown(note)}")
            lines.append("")

    append_jobs(
        "Eligible jobs — not yet applied",
        actionable_normal_jobs,
        logistics=False,
    )
    append_jobs(
        "Logistics and warehouse jobs — not yet applied",
        actionable_logistics_jobs,
        logistics=True,
    )
    append_jobs(
        "Already applied — reference only",
        already_applied_jobs,
        logistics=None,
        show_application_status=True,
    )

    lines.extend(["", "## Held out: non-numeric long-term commitment", ""])
    if report["non_numeric_long_term_jobs"]:
        lines.append(
            "These are excluded from the eligible count because the employer requires long-term availability but gives no verifiable one-, two-, or three-month minimum."
        )
        lines.append("")
        for record in report["non_numeric_long_term_jobs"]:
            lines.append(
                f"- [{escape_markdown(record['title'])}]({record['url']}) — "
                f"{escape_markdown(record['duration_requirement'])}; "
                f"{escape_markdown(record['area'])}"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## Clinical-study campaign ads requiring study-level review", ""])
    if report["clinical_studies"]:
        lines.append(
            "These are not counted as eligible jobs because the campaign pages do not verify one specific Sat/Sun schedule."
        )
        lines.append("")
        for record in report["clinical_studies"]:
            lines.extend(
                [
                    f"### [{escape_markdown(record['title'])}]({record['url']})",
                    "",
                    f"- Area/listing variants: {escape_markdown(', '.join(record['listed_areas']))}",
                    f"- Listings combined: {record['duplicate_listing_count']}",
                    f"- Compensation shown: {escape_markdown(record['salary_type'])} {escape_markdown(record['salary'])}",
                    "- Hard requirements: healthy adult who passes the selected study's medical screening and can attend its complete visit schedule",
                    "- Risk note: possible adverse effects are disclosed; exact exclusions, dates, visits, and compensation must be checked on the linked study before applying",
                    "",
                ]
            )
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Adult-only bar listings held out of the eligible set",
            "",
            f"- {len(report['restricted_bar_jobs'])} API results require signed-in adult verification, so their detail-page duration, exact Saturday-and-Sunday availability, and hard requirements could not be checked automatically.",
            "- A signed-in browser review of distinct weekend-advertised patterns found a mixture: some short/weekend possibilities, several hard 1–3 month commitments, and one Monday–Saturday schedule. They are therefore not treated as matches without individual verification.",
            f"- {report['restricted_bar_weekend_hint_listing_count']} listings have an explicit weekend hint in their list-level title or schedule, combined into {len(report['restricted_bar_weekend_hints'])} duplicate-free title/company groups below. All raw metadata and links are retained in the JSON artifact.",
            "",
            "### Weekend-hinted restricted listings",
            "",
        ]
    )
    if report["restricted_bar_weekend_hints"]:
        for record in report["restricted_bar_weekend_hints"]:
            lines.append(
                f"- [{escape_markdown(record['title'])}]({record['url']}) — "
                f"{escape_markdown(record['working_period'] or 'period not shown')}; "
                f"{escape_markdown(record['working_week'] or 'schedule not shown')}; "
                f"{record['duplicate_listing_count']} listing(s) in group"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## Screening summary", ""])
    for reason, count in report["exclusion_reason_counts"].items():
        lines.append(f"- {escape_markdown(reason)}: {count}")
    if report["detail_errors"]:
        lines.extend(["", "## Detail pages that could not be verified", ""])
        for error in report["detail_errors"]:
            lines.append(
                f"- [{error['recruit_no']}]({error['url']}): {escape_markdown(error['error'])}"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, "\n".join(lines) + "\n")


COMBINED_CSV_FIELDS = [
    "report_section",
    "already_applied",
    "applied_at",
    "applied_method",
    "application_read_status",
    "applied_recruit_no",
    "utility_rank",
    "utility_tier",
    "utility_score",
    "utility_reasons",
    "utility_cautions",
    "recruit_no",
    "duplicate_recruit_nos",
    "source_pages",
    "eligibility_lanes",
    "remote_job",
    "title",
    "company",
    "area",
    "working_address",
    "target_district",
    "district_proximity_penalty",
    "district_proximity_basis",
    "work_days",
    "work_week",
    "work_time",
    "weekend_shift_options",
    "target_date_options",
    "target_date_option_evidence",
    "late_night_compatible",
    "late_night_shift_options",
    "late_night_availability",
    "station_safety_nearest_station",
    "station_safety_walk_minutes",
    "station_safety_area_eligible",
    "start_time",
    "end_time",
    "ends_before_threshold",
    "has_daytime_shift_ending_before_threshold",
    "early_shift_evidence",
    "duration_requirement",
    "duration_evidence",
    "minimum_commitment_days",
    "availability_window_start_date",
    "availability_end_date",
    "availability_window_days",
    "availability_compatible",
    "availability_compatibility_basis",
    "hard_requirements",
    "certificate_requirements",
    "preferences",
    "salary_type",
    "salary",
    "structured_salary",
    "salary_notes",
    "salary_detail_evidence",
    "application_methods",
    "logistics_category",
    "cold_storage_status",
    "clinical_study",
    "url",
]


def csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return str(value)


def write_combined_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_text_writer(
        path, encoding="utf-8-sig", newline=""
    ) as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=COMBINED_CSV_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {field: csv_value(record.get(field)) for field in COMBINED_CSV_FIELDS}
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suit-no", type=int, default=DEFAULT_SUIT_NO)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--end-before", default="17:00")
    parser.add_argument(
        "--availability-end-date",
        type=date.fromisoformat,
        default=DEFAULT_AVAILABILITY_END_DATE,
        help=(
            "Last date on which work may be required (inclusive; default: "
            f"{DEFAULT_AVAILABILITY_END_DATE.isoformat()})."
        ),
    )
    parser.add_argument(
        "--special-source-cache",
        type=Path,
        default=DEFAULT_SPECIAL_SOURCE_CACHE_PATH,
        help="Cache for summer, urgent, remote, and station source membership.",
    )
    parser.add_argument(
        "--special-source-cache-hours",
        type=int,
        default=DEFAULT_SPECIAL_SOURCE_CACHE_HOURS,
        help="Refresh special-source membership after this many hours.",
    )
    parser.add_argument(
        "--no-special-source-cache",
        action="store_true",
        help="Fetch all special-source membership on every run.",
    )
    parser.add_argument(
        "--exclude-late-night",
        action="store_true",
        help="Omit the separately configured station-corridor evening lane.",
    )
    parser.add_argument(
        "--manual-reviews", type=Path, default=DEFAULT_MANUAL_REVIEWS_PATH
    )
    parser.add_argument(
        "--applied-history", type=Path, default=DEFAULT_APPLIED_HISTORY_PATH
    )
    parser.add_argument(
        "--detail-cache",
        type=Path,
        default=DEFAULT_DETAIL_CACHE_PATH,
        help="Persistent raw-detail cache used for incremental refreshes.",
    )
    parser.add_argument(
        "--no-detail-cache",
        action="store_true",
        help="Force a cold run without reading or writing the detail cache.",
    )
    parser.add_argument(
        "--cache-ttl-days",
        type=int,
        default=DEFAULT_EXCLUDED_TTL_DAYS,
        help="Recheck unchanged excluded postings after this many days.",
    )
    parser.add_argument(
        "--cache-retention-days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help="Drop unseen detail-cache entries after this many days.",
    )
    parser.add_argument(
        "--closing-refresh-hours",
        type=int,
        default=DEFAULT_CLOSING_REFRESH_HOURS,
        help="Recheck unchanged eligible postings closing within 72 hours.",
    )
    parser.add_argument(
        "--eligible-refresh-hours",
        type=int,
        default=DEFAULT_ELIGIBLE_REFRESH_HOURS,
        help="Recheck other unchanged eligible postings after this many hours.",
    )
    parser.add_argument(
        "--applied-refresh-hours",
        type=int,
        default=DEFAULT_APPLIED_REFRESH_HOURS,
        help="Recheck unchanged already-applied postings after this many hours.",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=100,
        help="Atomically checkpoint the detail cache after this many network jobs.",
    )
    parser.add_argument(
        "--input-report",
        type=Path,
        help="Regroup an existing combined JSON without refetching job listings.",
    )
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=Path("artifacts/albamon/latest-combined"),
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.page_size < 1 or args.page_size > 10_000:
        raise ValueError("--page-size must be between 1 and 10000")
    if args.special_source_cache_hours < 1:
        raise ValueError("--special-source-cache-hours must be at least 1")
    if args.workers < 1 or args.workers > 32:
        raise ValueError("--workers must be between 1 and 32")
    if args.cache_ttl_days < 1:
        raise ValueError("--cache-ttl-days must be at least 1")
    if args.cache_retention_days < args.cache_ttl_days:
        raise ValueError("--cache-retention-days must be at least --cache-ttl-days")
    if min(
        args.closing_refresh_hours,
        args.eligible_refresh_hours,
        args.applied_refresh_hours,
    ) < 1:
        raise ValueError("detail refresh intervals must be at least 1 hour")
    if args.closing_refresh_hours > args.eligible_refresh_hours:
        raise ValueError(
            "--closing-refresh-hours must not exceed --eligible-refresh-hours"
        )
    if args.checkpoint_every < 1:
        raise ValueError("--checkpoint-every must be at least 1")
    if parse_clock_minutes(args.end_before) is None:
        raise ValueError("--end-before must be a 24-hour time such as 17:00")

    if args.input_report:
        report = json.loads(args.input_report.read_text(encoding="utf-8"))
        closed_errors, report["detail_errors"] = split_closed_detail_errors(
            report.get("detail_errors") or []
        )
        prior_closed_count = int(report.get("closed_detail_listing_count") or 0)
        report["closed_detail_listing_count"] = max(
            prior_closed_count, len(closed_errors)
        )
        report.pop("closed_detail_recruit_nos", None)
        exclude_report_jobs_ending_on_or_before(
            report,
            as_of=datetime.now().astimezone(),
            availability_end=args.availability_end_date,
        )
        annotate_report_with_application_history(
            report, load_applied_history(args.applied_history)
        )
        write_combined_artifacts(args.output_prefix, report)
        return report

    performance: dict[str, Any] = {}
    applied_history = load_applied_history(args.applied_history)
    saved, source_jobs, source_totals = collect_source_jobs(
        suit_no=args.suit_no,
        page_size=args.page_size,
        performance_metrics=performance,
        special_source_cache=(
            None if args.no_special_source_cache else args.special_source_cache
        ),
        special_source_cache_hours=args.special_source_cache_hours,
        include_late_night=not args.exclude_late_night,
    )
    screening_started = time.perf_counter()
    merged_jobs, source_map, exact_duplicates = merge_by_recruit_no(source_jobs)
    source_parity = source_parity_summary(source_map)
    detail_candidates, pre_detail_excluded_jobs = prefilter_search_jobs(
        merged_jobs, source_map
    )
    detail_candidates, pre_detail_duplicates = deduplicate_search_jobs(
        detail_candidates, source_map
    )
    performance["pre_detail_screen_seconds"] = round(
        time.perf_counter() - screening_started, 3
    )
    performance["pre_detail_excluded"] = len(pre_detail_excluded_jobs)
    performance["pre_detail_duplicates_removed"] = pre_detail_duplicates

    detail_cache = None
    if not args.no_detail_cache:
        detail_cache = DetailCache.load(
            args.detail_cache,
            excluded_ttl_days=args.cache_ttl_days,
            retention_days=args.cache_retention_days,
            closing_refresh_hours=args.closing_refresh_hours,
            eligible_refresh_hours=args.eligible_refresh_hours,
            applied_refresh_hours=args.applied_refresh_hours,
            checkpoint_every=args.checkpoint_every,
        )
    detail_started = time.perf_counter()
    records, detail_errors = fetch_records(
        detail_candidates,
        workers=args.workers,
        end_before=args.end_before,
        manual_reviews=load_manual_reviews(args.manual_reviews),
        detail_cache=detail_cache,
        performance_metrics=performance,
        persist_detail_cache=False,
    )
    performance["detail_stage_seconds"] = round(
        time.perf_counter() - detail_started, 3
    )
    jobs_by_id = {
        int(job["recruitNo"]): job
        for job in detail_candidates
        if isinstance(job.get("recruitNo"), int)
    }
    restricted_bar_jobs, detail_errors = build_restricted_bar_jobs(
        detail_errors, jobs_by_id, source_map
    )
    closed_detail_errors, detail_errors = split_closed_detail_errors(detail_errors)
    restricted_weekend_hint_listings = [
        record for record in restricted_bar_jobs if record["weekend_hint"]
    ]
    restricted_bar_weekend_hints = deduplicate_restricted_weekend_hints(
        restricted_bar_jobs
    )

    classification_started = time.perf_counter()
    observed_at = datetime.now().astimezone()
    as_of_date = observed_at.date()
    availability_end = args.availability_end_date
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for record in records:
        search_job = jobs_by_id.get(record["recruit_no"], {})
        record["source_pages"] = source_map.get(record["recruit_no"], [])
        source_set = set(record["source_pages"])
        record["remote_job"] = "remote" in source_set
        record["station_safety_candidate"] = "station_safety" in source_set
        record["duplicate_recruit_nos"] = list(
            search_job.get("_pre_detail_duplicate_recruit_nos") or []
        )
        record["duplicate_urls"] = list(
            search_job.get("_pre_detail_duplicate_urls") or []
        )
        enrich_special_requirements(record)
        record["target_district"] = target_district(record)
        effective_end = effective_work_term_end(record, as_of=as_of_date)
        if effective_end and not record.get("work_term_end"):
            record["work_term_end"] = effective_end
            record["work_term_end_source"] = "posting title date range"
        (
            record["work_term_ended"],
            record["work_term_expiry_basis"],
        ) = record_work_term_has_expired(
            record, observed_at=observed_at
        )
        availability_reasons = availability_window_exclusion_reasons(
            record, as_of=as_of_date, availability_end=availability_end
        )
        record["availability_window_start_date"] = as_of_date.isoformat()
        record["availability_end_date"] = availability_end.isoformat()
        record["availability_window_days"] = max(
            0, (availability_end - as_of_date).days + 1
        )
        record["availability_compatible"] = not availability_reasons
        record["availability_compatibility_basis"] = (
            availability_reasons[0]
            if availability_reasons
            else availability_window_basis(
                record, as_of=as_of_date, availability_end=availability_end
            )
        )
        regular_reasons = combined_exclusion_reasons(
            record, as_of=as_of_date, availability_end=availability_end
        )
        station_only = bool(source_set) and source_set <= {"station_safety"}
        station_proximity = station_safety_proximity(record)
        record["station_safety_nearest_station"] = station_proximity[
            "nearest_station"
        ]
        record["station_safety_straight_line_km"] = station_proximity[
            "straight_line_km"
        ]
        record["station_safety_walk_minutes"] = station_proximity["walk_minutes"]
        record["station_safety_area_eligible"] = station_proximity["eligible"]
        record["station_safety_area_basis"] = station_proximity["basis"]
        if station_only and not station_proximity["eligible"]:
            regular_reasons.append(
                "station safety-net worksite is outside the "
                f"{EVENING_STATION_AREA_LIMIT_MINUTES}-minute target: "
                + station_proximity["basis"]
            )
        late_night_candidate = bool(
            source_set.intersection({"late_night", "station_safety"})
        )
        night_reasons = (
            late_night_exclusion_reasons(
                record, as_of=as_of_date, availability_end=availability_end
            )
            if late_night_candidate
            else []
        )
        if (
            station_only
            and late_night_candidate
            and not station_proximity["eligible"]
        ):
            night_reasons.append(
                "worksite is outside the "
                f"{EVENING_STATION_AREA_LIMIT_MINUTES}-minute target-station estimate: "
                + station_proximity["basis"]
            )
        remote_candidate = "remote" in source_set
        remote_reasons = (
            remote_exclusion_reasons(
                record, as_of=as_of_date, availability_end=availability_end
            )
            if remote_candidate
            else []
        )
        temporary_candidate = bool(
            set(record.get("target_date_options") or [])
            & {
                target.isoformat()
                for target in TEMPORARY_SINGLE_DAY_TARGET_DATES
            }
        )
        temporary_reasons = (
            temporary_single_day_exclusion_reasons(record, as_of=as_of_date)
            if temporary_candidate
            else []
        )
        record["weekend_lane_exclusion_reasons"] = regular_reasons
        record["late_night_lane_exclusion_reasons"] = night_reasons
        record["remote_lane_exclusion_reasons"] = remote_reasons
        record["temporary_single_day_lane_exclusion_reasons"] = temporary_reasons
        record["eligibility_lanes"] = []
        if not regular_reasons:
            record["eligibility_lanes"].append("weekend_short_term")
        if late_night_candidate and not night_reasons:
            record["eligibility_lanes"].append("songpa_late_night")
        if remote_candidate and not remote_reasons:
            record["eligibility_lanes"].append("remote_short_term")
        if temporary_candidate and not temporary_reasons:
            record["eligibility_lanes"].append(TEMPORARY_SINGLE_DAY_LANE)
        record["exclusion_reasons"] = []
        if not record["eligibility_lanes"]:
            record["exclusion_reasons"].extend(regular_reasons)
            record["exclusion_reasons"].extend(
                f"Station-corridor evening lane: {reason}"
                for reason in night_reasons
                if reason not in regular_reasons
            )
            record["exclusion_reasons"].extend(
                f"Remote lane: {reason}"
                for reason in remote_reasons
                if reason not in regular_reasons
            )
            record["exclusion_reasons"].extend(
                f"Configured single-day lane: {reason}"
                for reason in temporary_reasons
                if reason not in regular_reasons
            )
        if record["exclusion_reasons"]:
            excluded.append(record)
        else:
            eligible.append(record)

    excluded, expired_detail_records = prune_expired_audit_records(
        excluded, observed_at=observed_at
    )
    pre_detail_excluded_jobs, expired_pre_detail_records = (
        prune_expired_audit_records(
            pre_detail_excluded_jobs, observed_at=observed_at
        )
    )
    expired_recruit_nos = {
        int(record["recruit_no"])
        for record in [*expired_detail_records, *expired_pre_detail_records]
        if isinstance(record.get("recruit_no"), int)
    }

    deduplicated, semantic_duplicates = deduplicate_semantic(eligible)
    clinical_candidates = [
        record
        for record in excluded
        if record.get("clinical_study")
        and record["exclusion_reasons"] == [CLINICAL_STUDY_REVIEW_REASON]
    ]
    clinical_studies, clinical_duplicates = deduplicate_semantic(clinical_candidates)
    semantic_duplicates += clinical_duplicates
    non_numeric_long_term_jobs, long_term_duplicates = deduplicate_semantic(
        [
            record
            for record in excluded
            if record.get("duration_review_status") == "non_numeric_requirement"
            and record["exclusion_reasons"] == [record["duration_requirement"]]
        ]
    )
    semantic_duplicates += long_term_duplicates
    normal_jobs: list[dict[str, Any]] = []
    logistics_jobs: list[dict[str, Any]] = []
    for record in deduplicated:
        if record["logistics_job"]:
            record["report_section"] = "logistics"
            logistics_jobs.append(record)
        else:
            record["report_section"] = "jobs"
            normal_jobs.append(record)
    prioritize_records(normal_jobs)
    prioritize_records(logistics_jobs)

    exclusion_counts = Counter(
        reason for record in excluded for reason in record["exclusion_reasons"]
    )
    exclusion_counts.update(
        record["exclusion_reason"] for record in pre_detail_excluded_jobs
    )
    source_counts_text = ", ".join(
        f"{SOURCE_LABELS[source]} {source_totals[source]}"
        for source in SOURCE_ORDER
        if source in source_totals
    )
    report = {
        "run_id": uuid.uuid4().hex,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sources": {
            "suit": f"{SITE_URL}/jobs/suit",
            "multi_slot": (
                f"{GENERAL_API_URL}/recruit/search "
                "(서울 전체, 하루(1일), weekday unconstrained)"
            ),
            "late_night": (
                f"{GENERAL_API_URL}/recruit/search "
                f"({'·'.join(SETTINGS.albamon.evening.districts)}, short-term "
                "through the availability deadline, any weekday; detail-verified "
                f"{LATE_NIGHT_AVAILABILITY}; final "
                f"{EVENING_STATION_AREA_LIMIT_MINUTES}-minute station-area screening)"
            ),
            "station_safety": (
                f"{GENERAL_API_URL}/recruit/search "
                f"(exact {'·'.join(SETTINGS.albamon.evening.stations)} tags; "
                "list-level availability prefilter and coordinate verification)"
            ),
            "remote": REMOTE_URL,
            "summer": SUMMER_URL,
            "urgent": URGENT_URL,
            "search_api": f"{GENERAL_API_URL}/recruit/search",
        },
        "suit_no": args.suit_no,
        "saved_condition": saved,
        "target_districts": list(TARGET_DISTRICTS),
        "collection_areas": ["서울 전체"],
        "late_night_collection_area": "·".join(
            f"{district} 전체" for district in SETTINGS.albamon.evening.districts
        ),
        "late_night_station_corridor": list(EVENING_CORRIDOR_STATIONS),
        "late_night_station_area_limit_minutes": EVENING_STATION_AREA_LIMIT_MINUTES,
        "late_night_availability": LATE_NIGHT_AVAILABILITY,
        "availability_window_start_date": as_of_date.isoformat(),
        "availability_end_date": availability_end.isoformat(),
        "availability_window_days": max(
            0, (availability_end - as_of_date).days + 1
        ),
        "temporary_single_day_search": {
            "active": as_of_date in TEMPORARY_SINGLE_DAY_ACTIVE_DATES,
            "active_dates": sorted(
                value.isoformat() for value in TEMPORARY_SINGLE_DAY_ACTIVE_DATES
            ),
            "target_dates": sorted(
                value.isoformat() for value in TEMPORARY_SINGLE_DAY_TARGET_DATES
            ),
            "requires_independent_one_day_option": True,
        },
        "requested_age": SETTINGS.albamon.requested_age,
        "requested_gender": SETTINGS.albamon.requested_gender,
        "work_term_cutoff_date": as_of_date.isoformat(),
        "end_time_threshold": args.end_before,
        "utility_model": UTILITY_MODEL,
        "source_counts": source_totals,
        "configured_source_names": [
            source for source in SOURCE_ORDER if source in source_totals
        ],
        "source_parity": source_parity,
        "source_counts_text": source_counts_text,
        "source_result_count": sum(len(items) for items in source_jobs.values()),
        "unique_candidate_count": len(merged_jobs),
        "detail_candidate_count": len(detail_candidates),
        "pre_detail_excluded_count": len(pre_detail_excluded_jobs),
        "pre_detail_duplicates_removed": pre_detail_duplicates,
        "exact_cross_source_duplicates_removed": exact_duplicates,
        "semantic_duplicates_removed": semantic_duplicates,
        "duplicate_listings_removed": (
            exact_duplicates + pre_detail_duplicates + semantic_duplicates
        ),
        "eligible_count": len(normal_jobs) + len(logistics_jobs),
        "eligible_late_night_count": sum(
            "songpa_late_night" in (record.get("eligibility_lanes") or [])
            for record in [*normal_jobs, *logistics_jobs]
        ),
        "eligible_remote_count": sum(
            "remote_short_term" in (record.get("eligibility_lanes") or [])
            for record in [*normal_jobs, *logistics_jobs]
        ),
        "eligible_temporary_single_day_count": sum(
            TEMPORARY_SINGLE_DAY_LANE in (record.get("eligibility_lanes") or [])
            for record in [*normal_jobs, *logistics_jobs]
        ),
        "eligible_non_logistics_count": len(normal_jobs),
        "eligible_logistics_group_count": len(logistics_jobs),
        "excluded_count": len(excluded) + len(pre_detail_excluded_jobs),
        "expired_jobs_flushed_count": len(expired_recruit_nos),
        "expired_detail_jobs_flushed_count": len(expired_detail_records),
        "expired_pre_detail_jobs_flushed_count": len(
            expired_pre_detail_records
        ),
        "exclusion_reason_counts": dict(exclusion_counts.most_common()),
        "jobs": normal_jobs,
        "logistics_jobs": logistics_jobs,
        "non_numeric_long_term_jobs": non_numeric_long_term_jobs,
        "clinical_studies": clinical_studies,
        "excluded_jobs": excluded,
        "pre_detail_excluded_jobs": pre_detail_excluded_jobs,
        "restricted_bar_jobs": restricted_bar_jobs,
        "restricted_bar_weekend_hint_listing_count": len(
            restricted_weekend_hint_listings
        ),
        "restricted_bar_weekend_hints": restricted_bar_weekend_hints,
        "closed_detail_listing_count": len(closed_detail_errors),
        "detail_errors": detail_errors,
        "performance": performance,
    }

    annotate_report_with_application_history(
        report, applied_history
    )
    if detail_cache is not None:
        for record in excluded:
            detail_cache.mark_classification(record["recruit_no"], "excluded")
        # Mark every raw eligible listing, not only the semantic representative,
        # so a changed duplicate description is revalidated on the next run.
        for record in eligible:
            detail_cache.mark_classification(record["recruit_no"], "eligible")
        for record in [*report["jobs"], *report["logistics_jobs"]]:
            detail_cache.mark_classification(
                record["recruit_no"],
                "applied" if record.get("already_applied") else "eligible",
            )
        detail_cache.remove_many(
            {
                int(error["recruit_no"])
                for error in closed_detail_errors
                if isinstance(error, dict)
                and isinstance(error.get("recruit_no"), int)
            },
            reason="closed",
        )
        detail_cache.remove_many(expired_recruit_nos, reason="expired")
        detail_cache.prune(
            {
                int(job["recruitNo"])
                for job in detail_candidates
                if isinstance(job.get("recruitNo"), int)
            },
        )
        detail_cache.save()
        performance["detail_cache"] = detail_cache.metrics()
        performance["detail_cache_entries"] = len(detail_cache.entries)
    performance["classification_and_report_seconds"] = round(
        time.perf_counter() - classification_started, 3
    )
    write_combined_artifacts(args.output_prefix, report)
    return report


def write_combined_artifacts(prefix: Path, report: dict[str, Any]) -> None:
    """Write the JSON, CSV, and readable Markdown views of one report."""

    prefix.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        prefix.with_suffix(".json"),
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    write_combined_csv(
        prefix.with_suffix(".csv"),
        (report.get("jobs") or []) + (report.get("logistics_jobs") or []),
    )
    write_combined_markdown(prefix.with_suffix(".md"), report)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        report = run(args)
    except (AlbamonError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"Exported {report['eligible_count']} deduplicated jobs from "
        f"{report['unique_candidate_count']} unique candidate IDs "
        f"({len(report['detail_errors'])} detail errors)."
    )
    for suffix in (".json", ".csv", ".md"):
        print(args.output_prefix.with_suffix(suffix))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
