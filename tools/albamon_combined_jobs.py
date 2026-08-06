#!/usr/bin/env python3
"""Create one deduplicated Albamon report from 맞춤, 여름, and 급구 lists."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    from tools.albamon_cache import (
        DEFAULT_DETAIL_CACHE_PATH,
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
    )
except ModuleNotFoundError:  # Support direct execution from the repository root.
    from albamon_cache import (
        DEFAULT_DETAIL_CACHE_PATH,
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
    )


SUMMER_URL = (
    "https://www.albamon.com/jobs/guerrilla/summer-campaign"
    "?guerrillaBoothNo=35"
)
URGENT_URL = "https://www.albamon.com/jobs/urgent"
DEFAULT_APPLIED_HISTORY_PATH = Path("tools/albamon_applied_jobs.json")
SEOUL_DISTRICTS = (
    "강남구", "강동구", "강북구", "강서구", "관악구", "광진구", "구로구",
    "금천구", "노원구", "도봉구", "동대문구", "동작구", "마포구", "서대문구",
    "서초구", "성동구", "성북구", "송파구", "양천구", "영등포구", "용산구",
    "은평구", "종로구", "중구", "중랑구",
)
TARGET_DISTRICTS = SEOUL_DISTRICTS
SEOUL_ALL_AREAS = [
    {"si": "I000", "gu": "", "dong": "", "name": "서울 전체"}
]
REQUESTED_WORK_PERIOD_TYPES = [
    {"key": "ONE_DAY", "value": "5", "description": "하루(1일)"},
    {"key": "LESS_THAN_ONE_WEEK", "value": "10", "description": "1주일이하"},
    {
        "key": "ONE_WEEK_TO_ONE_MONTH",
        "value": "20",
        "description": "1주일~1개월",
    },
    {
        "key": "ONE_MONTH_TO_THREE_MONTH",
        "value": "30",
        "description": "1개월~3개월",
    },
]
REQUESTED_WORK_PERIOD_LABELS = {
    item["description"] for item in REQUESTED_WORK_PERIOD_TYPES
}
# Approximate proximity bands, centered on the user's preferred 강남구/송파구
# area. These are ranking weights, not claims about exact transit time.
DISTRICT_PROXIMITY_PENALTIES = {
    "강남구": 0,
    "송파구": 0,
    "서초구": -2,
    "강동구": -2,
    "광진구": -3,
    "성동구": -3,
    "동작구": -5,
    "용산구": -5,
    "중구": -5,
    "관악구": -6,
    "동대문구": -6,
    "성북구": -6,
    "영등포구": -8,
    "종로구": -8,
    "중랑구": -8,
    "구로구": -10,
    "금천구": -10,
    "마포구": -10,
    "서대문구": -10,
    "양천구": -12,
    "강북구": -12,
    "노원구": -12,
    "강서구": -14,
    "은평구": -14,
    "도봉구": -14,
}
SOURCE_LABELS = {
    "suit": "맞춤알바",
    "summer": "여름알바",
    "urgent": "급구알바",
}
ADULT_RESTRICTED_TEXT = "성인인증이 필요한 공고"
CLOSED_POSTING_ERROR_CODES = {"RCRV005", "RCRV006"}
CLINICAL_STUDY_REVIEW_REASON = (
    "clinical-study dates and medical eligibility are study-specific, not verified "
    "as Saturday-and-Sunday-only"
)
UTILITY_MODEL = {
    "purpose": "Prioritize practical weekend applications for the user's stated preferences",
    "neutral_factors": [
        "Shift end time, including before/after 17:00 and overnight work",
    ],
    "tier_thresholds": {"A": 95, "B": 82, "C": 68, "D": 0},
    "important_factors": [
        "verified weekend schedule",
        "verified short commitment",
        "advertised pay",
        "district proximity to Gangnam/Songpa",
        "hard-requirement burden",
        "application friction",
        "summer cold-work usefulness for B-Mart",
        "actual-center uncertainty for Coupang",
    ],
    "district_proximity_penalties": DISTRICT_PROXIMITY_PENALTIES,
    "confirmed_applicant_facts": ["Health certificate (보건증) held"],
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
    # user's approved duration buckets are an application rule, so enforce them
    # here instead of trusting whichever extra buckets the site returns later.
    filtered["condition"]["workPeriodTypes"] = copy.deepcopy(
        REQUESTED_WORK_PERIOD_TYPES
    )
    # The user explicitly wants Albamon's "협의포함" behavior at collection time.
    filtered["condition"]["excludeNegoWorkWeek"] = False
    # Adult/bar jobs are outside the requested search and previously accounted
    # for almost a third of all detail downloads.
    filtered["condition"]["excludeBar"] = True
    return filtered


def collect_source_jobs(
    *,
    suit_no: int,
    page_size: int,
    performance_metrics: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, int]]:
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as executor:
        saved_future = executor.submit(fetch_saved_condition, suit_no)
        summer_future = executor.submit(fetch_page_condition, SUMMER_URL)
        urgent_future = executor.submit(fetch_page_condition, URGENT_URL)
        saved = saved_future.result()
        summer_condition = summer_future.result()
        urgent_condition = urgent_future.result()
    conditions = {
        "suit": apply_saved_preferences(saved, saved),
        "summer": apply_saved_preferences(summer_condition, saved),
        "urgent": apply_saved_preferences(urgent_condition, saved),
    }
    source_jobs: dict[str, list[dict[str, Any]]] = {}
    source_totals: dict[str, int] = {}
    with GeneralApiClient() as api_client:
        with ThreadPoolExecutor(max_workers=3) as executor:
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
        if performance_metrics is not None:
            performance_metrics.update(
                {
                    "list_api_requests": api_client.requests,
                    "list_api_retries": api_client.retries,
                    "list_api_bytes_received": api_client.bytes_received,
                    "list_collection_seconds": round(
                        time.perf_counter() - started, 3
                    ),
                }
            )
    return saved, source_jobs, source_totals


def merge_by_recruit_no(
    source_jobs: dict[str, list[dict[str, Any]]]
) -> tuple[list[dict[str, Any]], dict[int, list[str]], int]:
    jobs_by_id: dict[int, dict[str, Any]] = {}
    sources_by_id: dict[int, set[str]] = defaultdict(set)
    raw_count = 0
    for source, jobs in source_jobs.items():
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


def pre_detail_exclusion_reason(job: dict[str, Any]) -> str | None:
    """Apply only exclusions that are conclusive in list-level metadata."""

    title = str(job.get("recruitTitle") or "")
    parts = " ".join(str(part) for part in job.get("parts") or [])
    if re.search(r"생동성|임상\s*시험|건강기능식품\s*시험", f"{title} {parts}"):
        return "clinical-study recruitment is outside the weekend job workflow"
    if re.search(
        r"운전면허\s*(?:필수|소지)|발렛|탁송|대리운전|"
        r"(?:배송|운전)\s*(?:기사|사원|직원|알바)",
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
        address
        and "서울" not in address
        and OUTSIDE_SEOUL_ADDRESS_RE.search(address)
        and coordinates_outside
    ):
        return "verified worksite address and coordinates are outside Seoul"
    return None


def prefilter_search_jobs(
    jobs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for job in jobs:
        reason = pre_detail_exclusion_reason(job)
        if reason is None:
            kept.append(job)
            continue
        recruit_no = int(job["recruitNo"])
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


def work_period_is_requested(record: dict[str, Any]) -> bool:
    """Reject only an explicit structured period outside the four user buckets."""

    work_period = str(record.get("work_period") or "").strip()
    return not work_period or work_period in REQUESTED_WORK_PERIOD_LABELS


def combined_exclusion_reasons(record: dict[str, Any]) -> list[str]:
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
        reasons.append(f"work term ends by {record['work_term_end']}")
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


def semantic_duplicate_key(record: dict[str, Any]) -> str:
    if record.get("is_coupang") and re.search(r"소분|분류", record.get("title", "")):
        return "coupang-cls-daily-sorting"
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
    return "|".join(
        (
            normalized_text(record.get("title")),
            normalized_text(record.get("company")),
            normalized_text(record.get("working_address")),
            normalized_text(record.get("work_time")),
        )
    )


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
            "approximate Seoul district proximity to Gangnam/Songpa; "
            "not exact transit time"
        )
        if proximity_penalty:
            adjust(
                proximity_penalty,
                f"{district} is farther from Gangnam/Songpa",
                caution=True,
            )
        else:
            adjust(0, f"{district} is a core preferred district")

    weekend_basis = str(record.get("weekend_compatibility_basis") or "")
    if weekend_basis.startswith("weekend") or weekend_basis == "explicit weekdays":
        adjust(10, "weekend schedule is explicit")
    else:
        adjust(3, "posting explicitly offers a negotiable weekend option")

    duration_status = record.get("duration_review_status")
    if duration_status == "verified_shorter":
        adjust(14, "commitment is verified shorter than one month")
    elif duration_status == "not_found":
        adjust(6, "no one-month minimum found")
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

    if score >= 95:
        tier = "A"
    elif score >= 82:
        tier = "B"
    elif score >= 68:
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


def effective_work_term_end(record: dict[str, Any], *, as_of: date) -> str | None:
    """Return the structured end date or infer an explicit work date from the title."""

    structured = record.get("work_term_end")
    if isinstance(structured, str) and structured:
        try:
            date.fromisoformat(structured)
        except ValueError:
            pass
        else:
            return structured

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
            elif str(record.get("work_period") or "") == "하루(1일)":
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
                    return None
                start_month = int(single.group(1))
                end_month = start_month
                end_day = int(single.group(2))
            else:
                return None
    end_year = as_of.year + (1 if end_month < start_month else 0)
    try:
        return date(end_year, end_month, end_day).isoformat()
    except ValueError:
        return None


def exclude_report_jobs_ending_on_or_before(
    report: dict[str, Any], *, as_of: date
) -> dict[str, Any]:
    """Apply the inclusive work-term cutoff to an already-generated report."""

    removed: list[dict[str, Any]] = []
    for section in ("jobs", "logistics_jobs"):
        kept: list[dict[str, Any]] = []
        for record in report.get(section) or []:
            effective_end = effective_work_term_end(record, as_of=as_of)
            if not work_term_ends_on_or_before(effective_end, as_of=as_of):
                kept.append(record)
                continue
            if not record.get("work_term_end"):
                record["work_term_end"] = effective_end
                record["work_term_end_source"] = "posting title date range"
            record["work_term_ended"] = True
            reason = f"work term ends by {record['work_term_end']}"
            reasons = record.setdefault("exclusion_reasons", [])
            if reason not in reasons:
                reasons.append(reason)
            removed.append(record)
        report[section] = kept
        prioritize_records(kept)

    excluded_jobs = report.setdefault("excluded_jobs", [])
    excluded_ids = {int(record["recruit_no"]) for record in excluded_jobs}
    excluded_jobs.extend(
        record for record in removed if int(record["recruit_no"]) not in excluded_ids
    )
    report["work_term_cutoff_date"] = as_of.isoformat()
    report["eligible_non_logistics_count"] = len(report.get("jobs") or [])
    report["eligible_logistics_group_count"] = len(
        report.get("logistics_jobs") or []
    )
    report["eligible_count"] = (
        report["eligible_non_logistics_count"]
        + report["eligible_logistics_group_count"]
    )
    report["excluded_count"] = len(excluded_jobs)
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
        "# Albamon combined 맞춤 · 여름 · 급구 report",
        "",
        f"Generated: {report['generated_at']}",
        "Target area: all 25 Seoul districts",
        "Weekend rule: weekend-only (Saturday and/or Sunday), including negotiable schedules only when the posting verifies a weekend option.",
        "Duration rule: Albamon buckets up to 1–3 months are collected, then postings with a verified hard minimum of one month or longer are excluded.",
        f"Work-term cutoff: jobs ending on or before {report.get('work_term_cutoff_date') or report['generated_at'][:10]} are excluded.",
        "Demographics: male age 26, including unrestricted listings; education is recorded as a hard requirement rather than silently assumed.",
        f"Source results after API filters: {report['source_counts_text']}",
        f"Unique recruit IDs inspected: {report['unique_candidate_count']}",
        f"Eligible non-logistics jobs: {len(normal_jobs)}",
        f"Eligible logistics groups: {len(logistics_jobs)}",
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
        "API verification: the 여름알바 and 급구알바 endpoints accept the same district, period, weekend-negotiation, gender, age, and education payload as 맞춤알바. Detail screening is still required because negotiated schedules and advertised locations are broad.",
        "Utility ranking: schedule certainty, short commitment, pay, district proximity to Gangnam/Songpa, hard requirements, application friction, summer cold-work usefulness, and logistics-location certainty. Shift end time (including before/after 17:00 and overnight work) is neutral. District proximity uses transparent approximate bands rather than exact transit-time claims.",
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
                    f"- Area: {escape_markdown(record['area'])}",
                    f"- Schedule: {escape_markdown(record['work_week'] or record['work_days'])}; {escape_markdown(record['work_time'])}",
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
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    "start_time",
    "end_time",
    "ends_before_threshold",
    "has_daytime_shift_ending_before_threshold",
    "early_shift_evidence",
    "duration_requirement",
    "duration_evidence",
    "hard_requirements",
    "certificate_requirements",
    "preferences",
    "salary_type",
    "salary",
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
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
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
    if args.workers < 1 or args.workers > 32:
        raise ValueError("--workers must be between 1 and 32")
    if args.cache_ttl_days < 1:
        raise ValueError("--cache-ttl-days must be at least 1")
    if args.cache_retention_days < args.cache_ttl_days:
        raise ValueError("--cache-retention-days must be at least --cache-ttl-days")
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
            report, as_of=datetime.now().astimezone().date()
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
    )
    screening_started = time.perf_counter()
    merged_jobs, source_map, exact_duplicates = merge_by_recruit_no(source_jobs)
    detail_candidates, pre_detail_excluded_jobs = prefilter_search_jobs(merged_jobs)
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
            checkpoint_every=args.checkpoint_every,
        )
    applied_ids = {
        int(recruit_no) for recruit_no in (applied_history.get("jobs") or {})
    }
    force_refresh_ids = {
        int(job["recruitNo"])
        for job in detail_candidates
        if {
            int(job["recruitNo"]),
            *(
                int(value)
                for value in job.get("_pre_detail_duplicate_recruit_nos") or []
            ),
        }
        & applied_ids
    }
    detail_started = time.perf_counter()
    records, detail_errors = fetch_records(
        detail_candidates,
        workers=args.workers,
        end_before=args.end_before,
        manual_reviews=load_manual_reviews(args.manual_reviews),
        detail_cache=detail_cache,
        force_refresh_ids=force_refresh_ids,
        performance_metrics=performance,
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
    as_of_date = datetime.now().astimezone().date()
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for record in records:
        search_job = jobs_by_id.get(record["recruit_no"], {})
        record["source_pages"] = source_map.get(record["recruit_no"], [])
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
        record["work_term_ended"] = work_term_ends_on_or_before(
            effective_end, as_of=as_of_date
        )
        record["exclusion_reasons"] = combined_exclusion_reasons(record)
        if record["exclusion_reasons"]:
            excluded.append(record)
        else:
            eligible.append(record)

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
        for source in ("suit", "summer", "urgent")
    )
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sources": {
            "suit": f"{SITE_URL}/jobs/suit",
            "summer": SUMMER_URL,
            "urgent": URGENT_URL,
            "search_api": f"{GENERAL_API_URL}/recruit/search",
        },
        "suit_no": args.suit_no,
        "saved_condition": saved,
        "target_districts": list(TARGET_DISTRICTS),
        "collection_areas": ["서울 전체"],
        "requested_age": 26,
        "requested_gender": "male or unrestricted",
        "work_term_cutoff_date": as_of_date.isoformat(),
        "end_time_threshold": args.end_before,
        "utility_model": UTILITY_MODEL,
        "source_counts": source_totals,
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
        "eligible_non_logistics_count": len(normal_jobs),
        "eligible_logistics_group_count": len(logistics_jobs),
        "excluded_count": len(excluded) + len(pre_detail_excluded_jobs),
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
        detail_cache.prune(
            {
                int(job["recruitNo"])
                for job in merged_jobs
                if isinstance(job.get("recruitNo"), int)
            }
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
    prefix.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
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
