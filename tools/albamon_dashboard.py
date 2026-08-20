#!/usr/bin/env python3
"""Serve a local decision dashboard from the latest Albamon reports.

The dashboard deliberately treats the generated JSON/CSV/Markdown artifacts as
the source of truth. It adds a read-only view over those artifacts plus a small,
reversible disposition editor for defer/do-not-apply choices.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import mimetypes
import re
import secrets
import threading
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

try:
    from tools import albamon_application_messages as application_messages
    from tools.albamon_pareto import annotate_pareto_frontier
    from tools.albamon_runtime import atomic_write_text, exclusive_file_lock
    from tools.dashboard_operations import (
        OperationCapacityError,
        OperationRegistry,
    )
    from tools.albamon_transit import (
        DEFAULT_KAKAO_QUOTA_STATE,
        DEFAULT_WORKERS,
        ORIGIN,
        ORIGIN_STATE_PATH,
        KakaoQuotaBudget,
        KakaoRequestPacer,
        build_cache,
        cache_origin_matches,
        lookup_commute_origin,
        origin_map_links,
        save_commute_origin,
        validate_commute_origin,
    )
    from tools.albamon_discord import notification_identity, validate_report_pair
    from tools.weekend_agencies.agencies import (
        hotel_agency_catalog_payload,
        hotel_agency_source_coverage_payload,
    )
    from tools.weekend_agencies.hotel_digest import aggregate_hotels
    from tools.weekend_agencies.pareto import annotate_hotel_frontiers
    from tools.weekend_agencies.config import (
        LOCATION_CACHE_PATH as WEEKEND_LOCATION_CACHE,
        LOCK_PATH as WEEKEND_REFRESH_LOCK,
        ROUTE_CACHE_PATH as WEEKEND_ROUTE_CACHE,
        SEARCH_DATES as MONITORED_HOTEL_DATES,
    )
    from tools.weekend_agencies.pipeline import reroute_report, run_pipeline
    from tools.weekend_agencies.report import write_report as write_weekend_report
    from tools.weekend_agencies.routing import read_api_key
except ModuleNotFoundError:  # Support ``python3 tools/albamon_dashboard.py``.
    import albamon_application_messages as application_messages
    from albamon_pareto import annotate_pareto_frontier
    from albamon_runtime import atomic_write_text, exclusive_file_lock
    from dashboard_operations import OperationCapacityError, OperationRegistry
    from albamon_transit import (
        DEFAULT_KAKAO_QUOTA_STATE,
        DEFAULT_WORKERS,
        ORIGIN,
        ORIGIN_STATE_PATH,
        KakaoQuotaBudget,
        KakaoRequestPacer,
        build_cache,
        cache_origin_matches,
        lookup_commute_origin,
        origin_map_links,
        save_commute_origin,
        validate_commute_origin,
    )
    from albamon_discord import notification_identity, validate_report_pair
    from weekend_agencies.agencies import (
        hotel_agency_catalog_payload,
        hotel_agency_source_coverage_payload,
    )
    from weekend_agencies.hotel_digest import aggregate_hotels
    from weekend_agencies.pareto import annotate_hotel_frontiers
    from weekend_agencies.config import (
        LOCATION_CACHE_PATH as WEEKEND_LOCATION_CACHE,
        LOCK_PATH as WEEKEND_REFRESH_LOCK,
        ROUTE_CACHE_PATH as WEEKEND_ROUTE_CACHE,
        SEARCH_DATES as MONITORED_HOTEL_DATES,
    )
    from weekend_agencies.pipeline import reroute_report, run_pipeline
    from weekend_agencies.report import write_report as write_weekend_report
    from weekend_agencies.routing import read_api_key


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
COMBINED_REPORT = ROOT / "artifacts/albamon/latest-combined.json"
UNIFIED_PREFIX = ROOT / "artifacts/albamon/latest-unified-report"
UNIFIED_REPORT = UNIFIED_PREFIX.with_suffix(".json")
TRANSIT_CACHE = ROOT / "tools/albamon_transit_cache.json"
MESSAGE_CACHE = ROOT / "artifacts/albamon/cache/application_messages.json"
DISCORD_STATE = ROOT / "artifacts/albamon/cache/discord_notifications.json"
DISPOSITIONS = ROOT / "tools/albamon_application_dispositions.json"
REVIEW_RESOLUTIONS = ROOT / "tools/albamon_review_resolutions.json"
WEEKEND_AGENCY_REPORT = ROOT / "artifacts/weekend-agencies/latest.json"
WEEKEND_AGENCY_EXPLORE_ROOT = ROOT / "artifacts/weekend-agencies/explore"
WEEKEND_AGENCY_EXPLORE_RUNS = WEEKEND_AGENCY_EXPLORE_ROOT / "runs"
WEEKEND_AGENCY_EXPLORE_CACHES = WEEKEND_AGENCY_EXPLORE_ROOT / "cache"
WEEKEND_AGENCY_EXPLORE_INDEX = WEEKEND_AGENCY_EXPLORE_ROOT / "index.json"
MAX_HOTEL_EXPLORATION_RUNS = 32
HOTEL_EXPLORATION_CACHE_HOURS = 4
MAX_HOTEL_EXPLORATION_DAYS = 31
ENV_FILE = ROOT / ".env"
ALBAMON_REFRESH_LOCK = ROOT / "artifacts/albamon/cache/refresh.lock"
NEW_WINDOW_HOURS = 24
CAPITAL_REGION_LATITUDE_BOUNDS = (36.8, 38.3)
CAPITAL_REGION_LONGITUDE_BOUNDS = (125.8, 128.3)
REPORT_NAMES = {
    "latest-unified-report.json": UNIFIED_REPORT,
    "latest-unified-report.csv": UNIFIED_PREFIX.with_suffix(".csv"),
    "latest-unified-report.md": UNIFIED_PREFIX.with_suffix(".md"),
    "latest-combined.json": COMBINED_REPORT,
    "latest-combined.csv": COMBINED_REPORT.with_suffix(".csv"),
    "latest-combined.md": COMBINED_REPORT.with_suffix(".md"),
    "weekend-agencies-latest.json": WEEKEND_AGENCY_REPORT,
    "weekend-agencies-latest.csv": WEEKEND_AGENCY_REPORT.with_suffix(".csv"),
    "weekend-agencies-latest.md": WEEKEND_AGENCY_REPORT.with_suffix(".md"),
}
STATIC_NAMES = {
    "/": WEB_ROOT / "index.html",
    "/index.html": WEB_ROOT / "index.html",
    "/app.js": WEB_ROOT / "app.js",
    "/job-view-model.mjs": WEB_ROOT / "job-view-model.mjs",
    "/hotel-pareto.mjs": WEB_ROOT / "hotel-pareto.mjs",
    "/hotel-view-model.mjs": WEB_ROOT / "hotel-view-model.mjs",
    "/point-layout.mjs": WEB_ROOT / "point-layout.mjs",
    "/safe-storage.mjs": WEB_ROOT / "safe-storage.mjs",
    "/albamon-pareto.mjs": WEB_ROOT / "albamon-pareto.mjs",
    "/styles.css": WEB_ROOT / "styles.css",
}
ALLOWED_DISPOSITIONS = {"apply", "maybe_later", "do_not_apply"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
WRITE_LOCK = threading.RLock()
HOTEL_REFRESH_LOCK = threading.Lock()
ORIGIN_REFRESH_LOCK = threading.Lock()
ROUTING_WORK_LOCK = threading.Lock()
KOREA_TIMEZONE = ZoneInfo("Asia/Seoul")
OPERATIONS = OperationRegistry(max_workers=2, max_history=32)
COMPRESSION_THRESHOLD = 1_024
SUMMARY_JOB_FIELDS = (
    "recruit_no",
    "company",
    "title",
    "url",
    "area",
    "working_address",
    "target_district",
    "latitude",
    "longitude",
    "application_disposition",
    "send_readiness",
    "already_applied",
    "salary_type",
    "salary",
    "comparable_hourly_pay",
    "work_hourly_pay",
    "pay_comparison_reliable",
    "pay_comparison_basis",
    "estimated_shift_earnings_won",
    "paid_shift_hours",
    "shift_span_hours",
    "door_to_door_hourly_pay",
    "total_time_commitment_minutes",
    "door_to_door_pay_basis",
    "work_days",
    "work_time",
    "work_time_option",
    "weekend_shift_options",
    "eligibility_lanes",
    "remote_job",
    "target_date_options",
    "target_date_option_evidence",
    "late_night_shift_options",
    "late_night_availability",
    "late_night_route_checked_at",
    "late_night_shift_journeys",
    "return_bicycle_minutes",
    "return_bicycle_distance_km",
    "salary_notes",
    "weekend_compatibility_basis",
    "weekend_evidence_type",
    "duration_requirement",
    "duration_review_status",
    "availability_window_start_date",
    "availability_end_date",
    "availability_window_days",
    "availability_compatibility_basis",
    "transit_minutes",
    "transit_transfers",
    "transit_vehicles",
    "transit_route_status",
    "transit_route_stale",
    "distance_excluded",
    "search_zone",
    "search_zone_label",
    "commute_limit_minutes",
    "commute_extension_minutes",
    "commute_policy_reason",
    "commute_policy_eligible",
    "pay_adjusted_commute",
    "evening_station_area_status",
    "evening_station_area_eligible",
    "evening_station_area_limit_minutes",
    "evening_nearest_station",
    "evening_station_distance_km",
    "evening_station_walk_minutes",
    "evening_station_area_basis",
    "utility_rank",
    "utility_tier",
    "utility_score",
    "pareto_frontier",
    "hard_requirements",
    "application_methods",
    "closing_at",
    "closing_label",
    "first_seen_at",
    "notified_at",
    "is_new",
    "is_closing_soon",
    "needs_review_reason",
    "review_reason_summary",
    "review_resolution_status",
    "review_resolved_at",
    "review_resolution_note",
    "logistics_category",
)
_CACHE_SIGNATURE: tuple[tuple[int, int], ...] | None = None
_CACHE_PAYLOAD: dict[str, Any] | None = None
_CACHE_DETAILS: dict[int, dict[str, Any]] | None = None
_HOTEL_CACHE_SIGNATURE: tuple[int, int] | None = None
_HOTEL_CACHE_PAYLOAD: dict[str, Any] | None = None


class DashboardDataError(RuntimeError):
    """Raised when a report needed by the dashboard cannot be read."""


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DashboardDataError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DashboardDataError(f"{label} contains invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise DashboardDataError(f"{label} must contain a JSON object: {path}")
    return payload


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.astimezone()


def _combined_records(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        record
        for record in [
            *(report.get("jobs") or []),
            *(report.get("logistics_jobs") or []),
        ]
        if isinstance(record, dict) and record.get("recruit_no") is not None
    ]


def _public_origin() -> dict[str, Any]:
    return {**dict(ORIGIN), "map_links": origin_map_links()}


def _notification_metadata(
    combined_record: dict[str, Any], state: dict[str, Any]
) -> dict[str, str]:
    jobs = state.get("jobs") if isinstance(state, dict) else None
    if not isinstance(jobs, dict):
        return {"first_seen_at": "", "notified_at": ""}
    entry = jobs.get(notification_identity(combined_record))
    if not isinstance(entry, dict):
        return {"first_seen_at": "", "notified_at": ""}
    return {
        "first_seen_at": str(entry.get("first_seen_at") or ""),
        "notified_at": str(entry.get("notified_at") or ""),
    }


def _is_closing_soon(record: dict[str, Any], now: datetime) -> bool:
    closing = _parse_datetime(record.get("closing_at"))
    if closing is None or str(record.get("closing_label") or "") == "상시모집":
        return False
    return now <= closing <= now + timedelta(hours=72)


def _weekend_evidence_type(record: dict[str, Any]) -> str:
    lanes = set(record.get("eligibility_lanes") or [])
    if record.get("remote_job") or "remote_short_term" in lanes:
        return "remote"
    if "songpa_late_night" in lanes and "weekend_short_term" not in lanes:
        return "late_night"
    if "aug_15_17_single_day" in lanes and "weekend_short_term" not in lanes:
        return "target_date"
    basis = str(record.get("weekend_compatibility_basis") or "").lower()
    work_days = str(record.get("work_days") or "")
    return "negotiable" if "negotiable" in basis or "협의" in work_days else "exact"


def _requirement_review_fingerprint(record: dict[str, Any]) -> str:
    evidence = {
        "send_readiness": record.get("send_readiness"),
        "fit_warning": record.get("fit_warning"),
        "hard_requirements": record.get("hard_requirements") or [],
        "certificate_requirements": record.get("certificate_requirements") or [],
        "verify_before_sending": record.get("verify_before_sending") or [],
    }
    encoded = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _matching_review_resolution(
    record: dict[str, Any], review_state: dict[str, Any]
) -> dict[str, Any]:
    if record.get("send_readiness") != "conditional_on_requirement":
        return {}
    jobs = review_state.get("jobs") if isinstance(review_state, dict) else None
    if not isinstance(jobs, dict):
        return {}
    entry = jobs.get(str(record.get("recruit_no")))
    if not isinstance(entry, dict):
        return {}
    if (
        entry.get("status") == "requirements_verified"
        and entry.get("requirements_fingerprint")
        == _requirement_review_fingerprint(record)
    ):
        return entry
    return {}


def _needs_review_reason(
    record: dict[str, Any], review_resolution: dict[str, Any] | None = None
) -> str:
    """Return one actionable review reason instead of counting settled skips."""

    disposition = str(record.get("application_disposition") or "")
    if disposition == "maybe_later":
        return "deferred"
    if (
        disposition == "apply"
        and record.get("send_readiness") == "conditional_on_requirement"
        and not review_resolution
    ):
        return "verify_requirements"
    return ""


def _review_reason_summary(record: dict[str, Any], reason: str) -> str:
    if reason == "deferred":
        return str(record.get("disposition_reason") or "You marked this job Maybe later")
    if reason != "verify_requirements":
        return ""
    requirements = [
        " ".join(str(value).split())
        for value in record.get("hard_requirements") or []
        if str(value).strip()
    ]
    if requirements:
        return "; ".join(requirements[:3])
    warning = " ".join(str(record.get("fit_warning") or "").split())
    return warning or "Check an unverified posting condition"


def _has_plausible_map_coordinates(record: dict[str, Any]) -> bool:
    try:
        latitude = float(record.get("latitude"))
        longitude = float(record.get("longitude"))
    except (TypeError, ValueError):
        return False
    return (
        CAPITAL_REGION_LATITUDE_BOUNDS[0]
        <= latitude
        <= CAPITAL_REGION_LATITUDE_BOUNDS[1]
        and CAPITAL_REGION_LONGITUDE_BOUNDS[0]
        <= longitude
        <= CAPITAL_REGION_LONGITUDE_BOUNDS[1]
    )


def _compact_job(record: dict[str, Any]) -> dict[str, Any]:
    compact = {field: record.get(field) for field in SUMMARY_JOB_FIELDS}
    if not _has_plausible_map_coordinates(compact):
        compact["latitude"] = None
        compact["longitude"] = None
    return compact


def build_dashboard_payload(
    combined_report: dict[str, Any],
    unified_report: dict[str, Any],
    discord_state: dict[str, Any] | None = None,
    review_state: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the compact initial payload used by the table and map."""

    payload, _details = build_dashboard_snapshot(
        combined_report,
        unified_report,
        discord_state,
        now=now,
        review_state=review_state,
    )
    return payload


def build_dashboard_snapshot(
    combined_report: dict[str, Any],
    unified_report: dict[str, Any],
    discord_state: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    route_origin_current: bool = True,
    review_state: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """Join decision, location, deadline, and notification data for the UI."""

    observed_at = now or datetime.now().astimezone()
    if observed_at.tzinfo is None:
        observed_at = observed_at.astimezone()
    state = discord_state or {}
    reviews = review_state or {}
    combined_by_id = {
        int(record["recruit_no"]): record for record in _combined_records(combined_report)
    }
    extra_fields = (
        "latitude",
        "longitude",
        "working_address",
        "target_district",
        "closing_at",
        "closing_label",
        "posted_at",
        "modified_at",
        "source_pages",
        "duplicate_listing_count",
        "duplicate_recruit_nos",
        "duration_evidence",
        "availability_window_start_date",
        "availability_end_date",
        "availability_window_days",
        "availability_compatibility_basis",
        "weekend_shift_options",
        "eligibility_lanes",
        "remote_job",
        "target_date_options",
        "target_date_option_evidence",
        "late_night_shift_options",
        "late_night_availability",
        "salary_detail_evidence",
        "structured_salary",
        "hard_requirement_evidence",
        "pre_application_questions",
        "preferences",
        "skills",
        "beginner_available",
    )
    jobs: list[dict[str, Any]] = []
    for unified in unified_report.get("jobs") or []:
        if not isinstance(unified, dict) or unified.get("recruit_no") is None:
            continue
        recruit_no = int(unified["recruit_no"])
        combined = combined_by_id.get(recruit_no, {})
        metadata = _notification_metadata(combined or unified, state)
        first_seen = _parse_datetime(metadata["first_seen_at"])
        is_new = bool(
            unified.get("application_disposition") == "apply"
            and first_seen is not None
            and observed_at - timedelta(hours=NEW_WINDOW_HOURS)
            <= first_seen
            <= observed_at
        )
        job = dict(unified)
        for field in extra_fields:
            job[field] = combined.get(field)
        job.update(metadata)
        job["is_new"] = is_new
        job["is_closing_soon"] = _is_closing_soon(combined, observed_at)
        job["weekend_evidence_type"] = _weekend_evidence_type(job)
        if not route_origin_current and not job.get("remote_job"):
            job["transit_minutes"] = None
            job["transit_transfers"] = None
            job["transit_vehicles"] = []
            job["transit_route_status"] = "stale_origin"
            job["distance_excluded"] = True
            job["distance_exclusion_reason"] = (
                "통근 출발지가 변경되어 버스·지하철 경로 재계산 필요"
            )
            if job.get("application_disposition") not in {
                "applied",
                "maybe_later",
                "do_not_apply",
            }:
                job["application_disposition"] = "excluded_by_distance"
        commute_policy = application_messages.commute_policy_for_job(
            job,
            {
                "route_status": job.get("transit_route_status"),
                "transit_minutes": job.get("transit_minutes"),
                "distance_excluded": job.get("distance_excluded"),
                "distance_exclusion_reason": job.get("distance_exclusion_reason"),
            },
            hourly_pay=int(job.get("comparable_hourly_pay") or 0),
        )
        for field, value in commute_policy.items():
            job.setdefault(field, value)
        review_resolution = _matching_review_resolution(job, reviews)
        job["review_resolution_status"] = str(
            review_resolution.get("status") or ""
        )
        job["review_resolved_at"] = str(
            review_resolution.get("resolved_at") or ""
        )
        job["review_resolution_note"] = str(
            review_resolution.get("note") or ""
        )
        job["needs_review_reason"] = _needs_review_reason(job, review_resolution)
        job["review_reason_summary"] = _review_reason_summary(
            job, job["needs_review_reason"]
        )
        jobs.append(job)

    pareto_frontier_count = annotate_pareto_frontier(jobs)
    actionable = [job for job in jobs if job.get("application_disposition") == "apply"]
    summary = {
        "new_actionable_count": sum(bool(job["is_new"]) for job in actionable),
        "actionable_count": len(actionable),
        "actionable_clear_count": sum(
            not job["needs_review_reason"] for job in actionable
        ),
        "closing_soon_count": sum(bool(job["is_closing_soon"]) for job in actionable),
        "needs_review_count": sum(bool(job["needs_review_reason"]) for job in jobs),
        "needs_review_breakdown": {
            "verify_requirements": sum(
                job["needs_review_reason"] == "verify_requirements" for job in jobs
            ),
            "deferred": sum(
                job["needs_review_reason"] == "deferred" for job in jobs
            ),
        },
        "requirements_verified_count": sum(
            job["review_resolution_status"] == "requirements_verified"
            for job in jobs
        ),
        "already_applied_count": sum(
            job.get("application_disposition") == "applied" for job in jobs
        ),
        "distance_excluded_count": sum(
            job.get("application_disposition") == "excluded_by_distance"
            for job in jobs
        ),
        "pay_adjusted_commute_count": sum(
            bool(job.get("pay_adjusted_commute")) for job in actionable
        ),
        "remote_actionable_count": sum(
            "remote_short_term" in (job.get("eligibility_lanes") or [])
            for job in actionable
        ),
        "evening_actionable_count": sum(
            "songpa_late_night" in (job.get("eligibility_lanes") or [])
            for job in actionable
        ),
        "pareto_frontier_count": pareto_frontier_count,
        "expired_jobs_flushed_count": int(
            combined_report.get("expired_jobs_flushed_count") or 0
        ),
        "source_result_count": int(combined_report.get("source_result_count") or 0),
        "eligible_count": int(combined_report.get("eligible_count") or len(jobs)),
    }
    payload = {
        "payload_version": 3,
        "generated_at": str(unified_report.get("generated_at") or ""),
        "source_generated_at": str(combined_report.get("generated_at") or ""),
        "availability_window_start_date": str(
            combined_report.get("availability_window_start_date") or ""
        ),
        "availability_end_date": str(
            combined_report.get("availability_end_date") or ""
        ),
        "availability_window_days": int(
            combined_report.get("availability_window_days") or 0
        ),
        "notification_state_updated_at": str(state.get("updated_at") or ""),
        "new_window_hours": NEW_WINDOW_HOURS,
        "origin": _public_origin(),
        "route_origin_current": route_origin_current,
        "summary": summary,
        "pareto": {
            "direction": "maximize_pay_minimize_commute",
            "scope": "actionable_jobs_with_verified_comparable_metrics",
            "comparison_modes": ["onsite", "remote"],
        },
        "jobs": [_compact_job(job) for job in jobs],
        "reports": [
            {"label": "Markdown", "url": "/reports/latest-unified-report.md"},
            {"label": "CSV", "url": "/reports/latest-unified-report.csv"},
            {"label": "JSON", "url": "/reports/latest-unified-report.json"},
        ],
    }
    return payload, {int(job["recruit_no"]): job for job in jobs}


def _path_signature(path: Path, *, optional: bool = False) -> tuple[int, int]:
    try:
        metadata = path.stat()
    except FileNotFoundError:
        if optional:
            return (0, 0)
        raise DashboardDataError(f"Dashboard source not found: {path}")
    return (metadata.st_mtime_ns, metadata.st_size)


def _dashboard_signature() -> tuple[tuple[int, int], ...]:
    return (
        _path_signature(COMBINED_REPORT),
        _path_signature(UNIFIED_REPORT),
        _path_signature(DISCORD_STATE, optional=True),
        _path_signature(TRANSIT_CACHE, optional=True),
        _path_signature(REVIEW_RESOLUTIONS, optional=True),
    )


def _invalidate_dashboard_cache() -> None:
    global _CACHE_SIGNATURE, _CACHE_PAYLOAD, _CACHE_DETAILS
    _CACHE_SIGNATURE = None
    _CACHE_PAYLOAD = None
    _CACHE_DETAILS = None


def load_dashboard_snapshot() -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    global _CACHE_SIGNATURE, _CACHE_PAYLOAD, _CACHE_DETAILS
    with WRITE_LOCK:
        signature = _dashboard_signature()
        if (
            signature == _CACHE_SIGNATURE
            and _CACHE_PAYLOAD is not None
            and _CACHE_DETAILS is not None
        ):
            return _CACHE_PAYLOAD, _CACHE_DETAILS
        combined = load_json_object(COMBINED_REPORT, label="Combined report")
        unified = load_json_object(UNIFIED_REPORT, label="Unified report")
        try:
            validate_report_pair(combined, unified)
        except ValueError as exc:
            raise DashboardDataError(str(exc)) from exc
        transit_cache = (
            load_json_object(TRANSIT_CACHE, label="Transit cache")
            if TRANSIT_CACHE.exists()
            else {}
        )
        state = (
            load_json_object(DISCORD_STATE, label="Discord notification state")
            if DISCORD_STATE.exists()
            else {}
        )
        review_state = (
            load_json_object(REVIEW_RESOLUTIONS, label="Review resolutions")
            if REVIEW_RESOLUTIONS.exists()
            else {}
        )
        payload, details = build_dashboard_snapshot(
            combined,
            unified,
            state,
            route_origin_current=cache_origin_matches(transit_cache),
            review_state=review_state,
        )
        _CACHE_SIGNATURE = signature
        _CACHE_PAYLOAD = payload
        _CACHE_DETAILS = details
        return payload, details


def load_dashboard_payload() -> dict[str, Any]:
    payload, _details = load_dashboard_snapshot()
    return payload


def build_weekend_agency_payload(
    report: dict[str, Any],
    *,
    now: datetime | None = None,
    exploration: bool = False,
    report_id: str = "",
) -> dict[str, Any]:
    """Build the lazy-loaded, hotel-level dashboard projection."""

    jobs = report.get("jobs") or []
    if not isinstance(jobs, list):
        raise DashboardDataError("Weekend agency report jobs must be a list")
    observed_at = now or datetime.now(KOREA_TIMEZONE)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=KOREA_TIMEZONE)
    else:
        observed_at = observed_at.astimezone(KOREA_TIMEZONE)
    observed_date = observed_at.date()
    current_jobs: list[dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        try:
            work_date = date.fromisoformat(str(job.get("work_date") or ""))
        except ValueError:
            continue
        if work_date >= observed_date:
            current_jobs.append(job)
    hotels = aggregate_hotels(current_jobs)
    frontier_counts = annotate_hotel_frontiers(hotels)
    sources = report.get("sources") if isinstance(report.get("sources"), dict) else {}
    policy = report.get("policy") if isinstance(report.get("policy"), dict) else {}
    report_catalog = report.get("agency_catalog")
    current_catalog = hotel_agency_catalog_payload()
    report_catalog_by_id = {
        str(entry.get("agency_id") or ""): dict(entry)
        for entry in report_catalog or []
        if isinstance(entry, dict) and entry.get("agency_id")
    }
    agency_catalog = []
    for current in current_catalog:
        merged = {**current, **report_catalog_by_id.get(current["agency_id"], {})}
        merged.pop("routine_competition", None)
        agency_catalog.append(merged)
    report_coverage = report.get("agency_source_coverage")
    agency_source_coverage = hotel_agency_source_coverage_payload()
    if isinstance(report_coverage, dict):
        agency_source_coverage.update(
            {
                str(agency_id): dict(entry)
                for agency_id, entry in report_coverage.items()
                if isinstance(entry, dict)
            }
        )
    generated_at = _parse_datetime(report.get("generated_at"))
    source_health = []
    for source_name, metrics in sources.items():
        if not isinstance(metrics, dict):
            continue
        checked_at = _parse_datetime(
            metrics.get("snapshot_checked_at") or report.get("generated_at")
        )
        age_minutes = (
            max(0, round((observed_at - checked_at.astimezone(KOREA_TIMEZONE)).total_seconds() / 60))
            if checked_at is not None
            else None
        )
        fetched = int(metrics.get("fetched_offer_count") or 0)
        raw_rows = metrics.get("raw_listing_rows")
        source_health.append(
            {
                "source": str(source_name),
                "status": str(metrics.get("status") or "unknown"),
                "checked_at": checked_at.isoformat(timespec="seconds") if checked_at else "",
                "age_minutes": age_minutes,
                "elapsed_seconds": metrics.get("elapsed_seconds"),
                "fetched_offer_count": fetched,
                "raw_listing_rows": raw_rows,
                "unique_listing_ids": metrics.get("unique_listing_ids"),
                "unique_open_listing_ids": metrics.get("unique_open_listing_ids"),
                "target_date_offer_count": metrics.get("target_date_offer_count"),
                "zero_result_confidence": (
                    "observed_empty"
                    if fetched == 0 and metrics.get("status") == "live"
                    else "has_results" if fetched > 0 else "stale_or_unknown"
                ),
            }
        )
    source_health.sort(key=lambda item: item["source"])
    if exploration and report_id:
        report_base = f"/reports/weekend-agencies-explore/{report_id}"
    else:
        report_base = "/reports/weekend-agencies-latest"
    return {
        "payload_version": 2,
        "run_id": str(report.get("run_id") or ""),
        "report_id": report_id,
        "generated_at": generated_at.isoformat(timespec="seconds") if generated_at else "",
        "all_sources_live": bool(report.get("all_sources_live")),
        "integrity_checks_passed": bool(report.get("integrity_checks_passed")),
        "policy": policy,
        "origin": {
            **dict(policy.get("origin") or ORIGIN),
            "map_links": origin_map_links(dict(policy.get("origin") or ORIGIN)),
        },
        "exploration": exploration,
        "monitored_dates": [value.isoformat() for value in MONITORED_HOTEL_DATES],
        "sources": sources,
        "source_health": source_health,
        "agency_catalog": agency_catalog,
        "agency_source_coverage": agency_source_coverage,
        "summary": {
            "hotel_count": len(hotels),
            "job_count": len(current_jobs),
            "expired_job_count_hidden": len(jobs) - len(current_jobs),
            "cross_agency_duplicate_count": sum(
                bool(job.get("cross_agency_duplicate")) for job in current_jobs
            ),
            "frontier_counts": frontier_counts,
            "weekly_allowance_rate_count": sum(
                bool(hotel.get("weekly_allowance_rate_stated")) for hotel in hotels
            ),
        },
        "pareto": {
            "direction": "maximize_pay_minimize_commute",
            "actual_pay_field": "actual_hourly_max",
            "weekly_allowance_pay_field": "pareto_weekly_allowance_pay",
            "weekly_allowance_fallback": "actual_hourly_max",
            "weekly_allowance_is_conditional": True,
        },
        "hotels": hotels,
        "reports": [
            {"label": "Markdown", "url": f"{report_base}.md"},
            {"label": "CSV", "url": f"{report_base}.csv"},
            {"label": "JSON", "url": f"{report_base}.json"},
        ],
    }


def load_weekend_agency_payload() -> dict[str, Any]:
    """Load and cache the separate hotel report without slowing Albamon startup."""

    global _HOTEL_CACHE_SIGNATURE, _HOTEL_CACHE_PAYLOAD
    with WRITE_LOCK:
        signature = _path_signature(WEEKEND_AGENCY_REPORT)
        if signature == _HOTEL_CACHE_SIGNATURE and _HOTEL_CACHE_PAYLOAD is not None:
            return _HOTEL_CACHE_PAYLOAD
        report = load_json_object(WEEKEND_AGENCY_REPORT, label="Weekend agency report")
        payload = build_weekend_agency_payload(report)
        _HOTEL_CACHE_SIGNATURE = signature
        _HOTEL_CACHE_PAYLOAD = payload
        return payload


def _search_dates_from_payload(
    payload: dict[str, Any], *, today: date
) -> tuple[date, ...]:
    try:
        start = date.fromisoformat(str(payload.get("start_date") or ""))
        end = date.fromisoformat(str(payload.get("end_date") or ""))
    except ValueError as exc:
        raise ValueError("Use valid ISO start and end dates") from exc
    if start < today:
        raise ValueError("Search start cannot be earlier than today")
    if end < start:
        raise ValueError("Search end must not precede search start")
    if (end - start).days + 1 > MAX_HOTEL_EXPLORATION_DAYS:
        raise ValueError(
            f"Search at most {MAX_HOTEL_EXPLORATION_DAYS} days at a time"
        )
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _korea_datetime(value: datetime | None = None) -> datetime:
    observed = value or datetime.now(KOREA_TIMEZONE)
    if observed.tzinfo is None:
        return observed.replace(tzinfo=KOREA_TIMEZONE)
    return observed.astimezone(KOREA_TIMEZONE)


def _exploration_signature(
    search_dates: tuple[date, ...], *, origin: dict[str, Any] | None = None
) -> str:
    route_origin = validate_commute_origin(origin or ORIGIN)
    material = {
        "search_dates": [value.isoformat() for value in sorted(set(search_dates))],
        "origin": route_origin,
        "weekends_only": False,
        "minimum_actual_hourly_won": 12_000,
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _exploration_source_signature(search_dates: tuple[date, ...]) -> str:
    """Key source HTML separately from route-origin-dependent reports."""

    material = {
        "search_dates": [value.isoformat() for value in sorted(set(search_dates))],
        "weekends_only": False,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _exploration_prefix(report_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{20}", str(report_id)):
        raise ValueError("Invalid hotel exploration report ID")
    return WEEKEND_AGENCY_EXPLORE_RUNS / str(report_id)


def _load_exploration_index() -> dict[str, Any]:
    if not WEEKEND_AGENCY_EXPLORE_INDEX.exists():
        return {"version": 1, "searches": {}}
    try:
        payload = json.loads(WEEKEND_AGENCY_EXPLORE_INDEX.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "searches": {}}
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return {"version": 1, "searches": {}}
    if not isinstance(payload.get("searches"), dict):
        payload["searches"] = {}
    return payload


def _publish_exploration_index(
    signature: str,
    *,
    report_id: str,
    generated_at: str,
) -> None:
    index = _load_exploration_index()
    searches = index.setdefault("searches", {})
    searches[signature] = {
        "report_id": report_id,
        "generated_at": generated_at,
    }
    ranked = sorted(
        (
            (key, value)
            for key, value in searches.items()
            if isinstance(value, dict) and value.get("report_id")
        ),
        key=lambda item: str(item[1].get("generated_at") or ""),
        reverse=True,
    )
    retained = dict(ranked[:MAX_HOTEL_EXPLORATION_RUNS])
    index["searches"] = retained
    index["updated_at"] = _korea_datetime().isoformat(timespec="seconds")
    atomic_write_text(
        WEEKEND_AGENCY_EXPLORE_INDEX,
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
    )
    active_ids = {
        str(value.get("report_id"))
        for value in retained.values()
        if isinstance(value, dict)
    }
    run_files = sorted(
        (
            path
            for path in WEEKEND_AGENCY_EXPLORE_RUNS.glob("*.json")
            if re.fullmatch(r"[0-9a-f]{20}", path.stem)
        ),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    retained_run_ids = {
        active_id
        for active_id in active_ids
        if re.fullmatch(r"[0-9a-f]{20}", active_id)
        and _exploration_prefix(active_id).with_suffix(".json").is_file()
    }
    for path in run_files:
        if len(retained_run_ids) >= MAX_HOTEL_EXPLORATION_RUNS:
            break
        retained_run_ids.add(path.stem)
    for path in run_files:
        if path.stem in retained_run_ids:
            continue
        prefix = _exploration_prefix(path.stem)
        for suffix in (".json", ".csv", ".md"):
            artifact = prefix.with_suffix(suffix)
            if artifact.is_file() and artifact.parent == WEEKEND_AGENCY_EXPLORE_RUNS:
                artifact.unlink()


def _cached_exploration(
    signature: str, *, observed_at: datetime
) -> tuple[dict[str, Any], str] | None:
    entry = (_load_exploration_index().get("searches") or {}).get(signature)
    if not isinstance(entry, dict):
        return None
    report_id = str(entry.get("report_id") or "")
    if not re.fullmatch(r"[0-9a-f]{20}", report_id):
        return None
    path = _exploration_prefix(report_id).with_suffix(".json")
    if not path.is_file():
        return None
    report = load_json_object(path, label="Hotel exploration report")
    generated_at = _parse_datetime(report.get("generated_at"))
    if generated_at is None:
        return None
    if observed_at - generated_at.astimezone(KOREA_TIMEZONE) > timedelta(
        hours=HOTEL_EXPLORATION_CACHE_HOURS
    ):
        return None
    return report, report_id


def search_weekend_agencies(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    operation_progress: Any | None = None,
) -> dict[str, Any]:
    """Run a non-notifying, independently cached hotel date exploration."""

    observed_at = _korea_datetime(now)
    search_dates = _search_dates_from_payload(payload, today=observed_at.date())
    signature = _exploration_signature(search_dates)
    notify = operation_progress or (lambda _percent, _message, _detail=None: None)
    with (
        HOTEL_REFRESH_LOCK,
        ROUTING_WORK_LOCK,
        exclusive_file_lock(_workspace_path(WEEKEND_REFRESH_LOCK)),
    ):
        if payload.get("force") is not True:
            cached = _cached_exploration(signature, observed_at=observed_at)
            if cached is not None:
                report, report_id = cached
                notify(95, "Loaded a recent matching search", {"cache": "hit"})
                return build_weekend_agency_payload(
                    report,
                    now=observed_at,
                    exploration=True,
                    report_id=report_id,
                )
        report_id = secrets.token_hex(10)
        output_prefix = _exploration_prefix(report_id)
        source_cache = (
            WEEKEND_AGENCY_EXPLORE_CACHES
            / _exploration_source_signature(search_dates)
            / "source_snapshots.json"
        )

        def pipeline_progress(
            stage: str,
            completed: int,
            total: int,
            detail: dict[str, Any],
        ) -> None:
            if stage == "source_start":
                percent = 5 + round(55 * completed / max(1, total))
                message = f"Checking {detail.get('source') or 'source'}"
            elif stage == "source_complete":
                percent = 5 + round(55 * completed / max(1, total))
                message = f"Finished {detail.get('source') or 'source'}"
            elif stage == "routing_start":
                percent = 70
                message = "Calculating verified commutes"
            else:
                percent = 90
                message = "Building the hotel comparison"
            notify(percent, message, {"stage": stage, **detail})

        report, _paths = run_pipeline(
            api_key=read_api_key(ENV_FILE),
            output_prefix=output_prefix,
            source_cache_path=source_cache,
            search_dates=search_dates,
            weekends_only=False,
            now=observed_at,
            origin=dict(ORIGIN),
            progress=pipeline_progress,
        )
        notify(94, "Publishing immutable results", {"report_id": report_id})
        with WRITE_LOCK:
            _publish_exploration_index(
                signature,
                report_id=report_id,
                generated_at=str(report.get("generated_at") or ""),
            )
        result = build_weekend_agency_payload(
            report,
            now=observed_at,
            exploration=True,
            report_id=report_id,
        )
        return result


def start_weekend_agency_search(payload: dict[str, Any]) -> dict[str, Any]:
    observed_at = _korea_datetime()
    dates = _search_dates_from_payload(payload, today=observed_at.date())
    key = f"hotel-search:{_exploration_signature(dates)}"
    request = dict(payload)

    def task(progress: Any) -> dict[str, Any]:
        return search_weekend_agencies(
            request,
            now=observed_at,
            operation_progress=progress,
        )

    return OPERATIONS.start(kind="hotel_search", key=key, task=task)


def resolve_origin_address(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = read_api_key(ENV_FILE)
    if not api_key:
        raise ValueError("KAKAO_REST_API_KEY is required for address search")
    quota_path = (
        DEFAULT_KAKAO_QUOTA_STATE
        if DEFAULT_KAKAO_QUOTA_STATE.is_absolute()
        else ROOT / DEFAULT_KAKAO_QUOTA_STATE
    )
    origin = lookup_commute_origin(
        api_key=api_key,
        query=str(payload.get("query") or ""),
        quota_budget=KakaoQuotaBudget(quota_path),
        request_pacer=KakaoRequestPacer(),
    )
    return {"origin": {**origin, "map_links": origin_map_links(origin)}}


def _workspace_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def _snapshot_text_files(paths: list[Path]) -> dict[Path, str | None]:
    return {
        path: path.read_text(encoding="utf-8") if path.is_file() else None
        for path in paths
    }


def _restore_text_files(snapshots: dict[Path, str | None]) -> None:
    for path, content in snapshots.items():
        if content is None:
            if path.is_file():
                path.unlink()
        else:
            atomic_write_text(path, content)


def update_commute_origin(
    payload: dict[str, Any], *, operation_progress: Any | None = None
) -> dict[str, Any]:
    """Transactionally publish a confirmed origin and all dependent reports."""

    global _HOTEL_CACHE_SIGNATURE, _HOTEL_CACHE_PAYLOAD
    if payload.get("recalculate") is not True:
        raise ValueError("Confirm commute recalculation before changing the origin")
    candidate = validate_commute_origin(payload)
    previous_origin = dict(ORIGIN)
    api_key = read_api_key(ENV_FILE)
    if not api_key:
        raise ValueError("KAKAO_REST_API_KEY is required to recalculate commutes")
    quota_path = (
        DEFAULT_KAKAO_QUOTA_STATE
        if DEFAULT_KAKAO_QUOTA_STATE.is_absolute()
        else ROOT / DEFAULT_KAKAO_QUOTA_STATE
    )
    notify = operation_progress or (lambda _percent, _message, _detail=None: None)
    affected_paths = [
        ORIGIN_STATE_PATH,
        TRANSIT_CACHE,
        UNIFIED_REPORT,
        UNIFIED_PREFIX.with_suffix(".csv"),
        UNIFIED_PREFIX.with_suffix(".md"),
        MESSAGE_CACHE,
        WEEKEND_AGENCY_REPORT,
        WEEKEND_AGENCY_REPORT.with_suffix(".csv"),
        WEEKEND_AGENCY_REPORT.with_suffix(".md"),
        WEEKEND_AGENCY_EXPLORE_INDEX,
        _workspace_path(WEEKEND_LOCATION_CACHE),
        _workspace_path(WEEKEND_ROUTE_CACHE),
    ]
    with (
        ORIGIN_REFRESH_LOCK,
        ROUTING_WORK_LOCK,
        exclusive_file_lock(ALBAMON_REFRESH_LOCK),
        exclusive_file_lock(_workspace_path(WEEKEND_REFRESH_LOCK)),
    ):
        snapshots = _snapshot_text_files(affected_paths)
        notify(5, "Loading current reports", None)
        combined = load_json_object(COMBINED_REPORT, label="Combined report")
        existing_cache = (
            load_json_object(TRANSIT_CACHE, label="Transit cache")
            if TRANSIT_CACHE.exists()
            else None
        )
        cache = build_cache(
            combined,
            api_key=api_key,
            workers=DEFAULT_WORKERS,
            existing_cache=existing_cache,
            quota_budget=KakaoQuotaBudget(quota_path),
            defer_unverified_routes=True,
            origin=candidate,
        )
        notify(
            55,
            "Albamon commutes recalculated",
            {"routes": int(cache.get("unique_route_count") or 0)},
        )
        rerouted_reports: list[str] = []
        rerouted_hotel: dict[str, Any] | None = None
        if WEEKEND_AGENCY_REPORT.exists():
            notify(65, "Recalculating monitored hotel commutes", None)
            hotel_report = load_json_object(
                WEEKEND_AGENCY_REPORT, label="Hotel agency report"
            )
            try:
                rerouted_hotel, _paths = reroute_report(
                    hotel_report,
                    api_key=api_key,
                    output_prefix=WEEKEND_AGENCY_REPORT.with_suffix(""),
                    write_outputs=False,
                    origin=candidate,
                )
            except Exception:
                _restore_text_files(
                    {
                        path: snapshots[path]
                        for path in (
                            _workspace_path(WEEKEND_LOCATION_CACHE),
                            _workspace_path(WEEKEND_ROUTE_CACHE),
                        )
                    }
                )
                raise
            rerouted_reports.append(WEEKEND_AGENCY_REPORT.name)

        notify(90, "Publishing the new origin and reports", None)
        try:
            with WRITE_LOCK:
                saved = save_commute_origin(candidate, path=ORIGIN_STATE_PATH)
                atomic_write_text(
                    TRANSIT_CACHE,
                    json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
                )
                rebuild_unified_report()
                if rerouted_hotel is not None:
                    write_weekend_report(
                        WEEKEND_AGENCY_REPORT.with_suffix(""), rerouted_hotel
                    )
                # Existing exploration reports remain immutable and honest about
                # their old origin, but cannot be selected as a current cache hit.
                atomic_write_text(
                    WEEKEND_AGENCY_EXPLORE_INDEX,
                    json.dumps(
                        {
                            "version": 1,
                            "searches": {},
                            "invalidated_at": _korea_datetime().isoformat(
                                timespec="seconds"
                            ),
                            "reason": "commute_origin_changed",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                )
                _HOTEL_CACHE_SIGNATURE = None
                _HOTEL_CACHE_PAYLOAD = None
        except Exception:
            with WRITE_LOCK:
                _restore_text_files(snapshots)
                ORIGIN.clear()
                ORIGIN.update(previous_origin)
                _invalidate_dashboard_cache()
                _HOTEL_CACHE_SIGNATURE = None
                _HOTEL_CACHE_PAYLOAD = None
            raise
    return {
        "ok": True,
        "origin": {**saved, "map_links": origin_map_links(saved)},
        "albamon_routes": int(cache.get("unique_route_count") or 0),
        "webui_fallback_required": int(
            cache.get("webui_fallback_required_count") or 0
        ),
        "rerouted_hotel_reports": rerouted_reports,
    }


def start_commute_origin_update(payload: dict[str, Any]) -> dict[str, Any]:
    candidate = validate_commute_origin(payload)
    if payload.get("recalculate") is not True:
        raise ValueError("Confirm commute recalculation before changing the origin")
    fingerprint = hashlib.sha256(
        json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    request = dict(payload)

    def task(progress: Any) -> dict[str, Any]:
        return update_commute_origin(request, operation_progress=progress)

    return OPERATIONS.start(
        kind="origin_recalculation",
        key=f"origin:{fingerprint}",
        task=task,
    )


def load_job_detail(recruit_no: int) -> dict[str, Any] | None:
    _payload, details = load_dashboard_snapshot()
    return details.get(recruit_no)


def encode_response_body(data: bytes, accept_encoding: str) -> tuple[bytes, str]:
    """Compress substantial local responses when the browser supports gzip."""

    accepted = {item.strip().split(";", 1)[0] for item in accept_encoding.split(",")}
    if len(data) < COMPRESSION_THRESHOLD or "gzip" not in accepted:
        return data, ""
    return gzip.compress(data, compresslevel=5), "gzip"


def write_disposition(
    path: Path,
    recruit_no: int,
    status: str,
    reason: str,
    *,
    allowed_recruit_nos: set[int] | None = None,
    now: datetime | None = None,
) -> None:
    """Persist one reversible user disposition with bounded input."""

    if recruit_no <= 0 or (
        allowed_recruit_nos is not None and recruit_no not in allowed_recruit_nos
    ):
        raise ValueError("Unknown job ID")
    if status not in ALLOWED_DISPOSITIONS:
        raise ValueError("Unsupported disposition")
    clean_reason = " ".join(str(reason or "").split())
    if len(clean_reason) > 500:
        raise ValueError("Reason must be 500 characters or fewer")
    if status != "apply" and not clean_reason:
        raise ValueError("Add a short reason before deferring or skipping a job")

    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Application dispositions must contain a JSON object")
    else:
        payload = {}
    key = str(recruit_no)
    if status == "apply":
        payload.pop(key, None)
    else:
        recorded_at = (now or datetime.now().astimezone()).date().isoformat()
        payload[key] = {
            "status": status,
            "reason": clean_reason,
            "recorded_at": recorded_at,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def write_review_resolution(
    path: Path,
    recruit_no: int,
    action: str,
    requirements_fingerprint: str,
    note: str = "",
    *,
    allowed_recruit_nos: set[int] | None = None,
    now: datetime | None = None,
) -> None:
    """Persist or reopen a requirement acknowledgement for one exact job."""

    if recruit_no <= 0 or (
        allowed_recruit_nos is not None and recruit_no not in allowed_recruit_nos
    ):
        raise ValueError("Unknown job ID")
    if action not in {"confirm_requirements", "reopen_requirements"}:
        raise ValueError("Unsupported review action")
    clean_note = " ".join(str(note or "").split())
    if len(clean_note) > 500:
        raise ValueError("Review note must be 500 characters or fewer")
    if action == "confirm_requirements" and not requirements_fingerprint:
        raise ValueError("Requirement evidence fingerprint is missing")

    payload: dict[str, Any]
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Review resolutions must contain a JSON object")
    else:
        payload = {"version": 1, "jobs": {}}
    jobs = payload.setdefault("jobs", {})
    if not isinstance(jobs, dict):
        raise ValueError("Review resolutions must contain a jobs object")
    if allowed_recruit_nos is not None:
        for key in list(jobs):
            try:
                active = int(key) in allowed_recruit_nos
            except (TypeError, ValueError):
                active = False
            if not active:
                jobs.pop(key, None)
    key = str(recruit_no)
    if action == "reopen_requirements":
        jobs.pop(key, None)
    else:
        jobs[key] = {
            "status": "requirements_verified",
            "requirements_fingerprint": requirements_fingerprint,
            "resolved_at": (now or datetime.now().astimezone()).isoformat(
                timespec="seconds"
            ),
            "note": clean_note,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def rebuild_unified_report() -> None:
    """Regenerate every existing unified report format after a disposition edit."""

    combined = load_json_object(COMBINED_REPORT, label="Combined report")
    metrics: dict[str, int] = {}
    records = application_messages.build_records(
        combined,
        application_messages.load_dispositions(DISPOSITIONS),
        application_messages.load_transit_cache(TRANSIT_CACHE),
        application_messages.load_message_cache(MESSAGE_CACHE),
        metrics,
    )
    UNIFIED_PREFIX.parent.mkdir(parents=True, exist_ok=True)
    application_messages.write_json(
        UNIFIED_REPORT,
        COMBINED_REPORT,
        records,
        source_report=combined,
    )
    application_messages.write_csv(UNIFIED_PREFIX.with_suffix(".csv"), records)
    application_messages.write_markdown(UNIFIED_PREFIX.with_suffix(".md"), records)
    application_messages.write_message_cache(MESSAGE_CACHE, records)
    _invalidate_dashboard_cache()


def update_job_disposition(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        recruit_no = int(payload.get("recruit_no"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Job ID must be an integer") from exc
    status = str(payload.get("status") or "")
    reason = str(payload.get("reason") or "")
    with WRITE_LOCK:
        combined = load_json_object(COMBINED_REPORT, label="Combined report")
        allowed_ids = {int(record["recruit_no"]) for record in _combined_records(combined)}
        write_disposition(
            DISPOSITIONS,
            recruit_no,
            status,
            reason,
            allowed_recruit_nos=allowed_ids,
        )
        rebuild_unified_report()
    return {"ok": True, "recruit_no": recruit_no, "status": status}


def update_review_resolution(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        recruit_no = int(payload.get("recruit_no"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Job ID must be an integer") from exc
    action = str(payload.get("action") or "")
    note = str(payload.get("note") or "")
    with WRITE_LOCK:
        unified = load_json_object(UNIFIED_REPORT, label="Unified report")
        current_jobs = [
            record for record in unified.get("jobs") or [] if isinstance(record, dict)
        ]
        job = next(
            (
                record
                for record in current_jobs
                if int(record.get("recruit_no") or 0) == recruit_no
            ),
            None,
        )
        if job is None:
            raise ValueError("Unknown job ID")
        if action == "confirm_requirements" and not (
            job.get("application_disposition") == "apply"
            and job.get("send_readiness") == "conditional_on_requirement"
        ):
            raise ValueError("This job does not have an open requirement review")
        write_review_resolution(
            REVIEW_RESOLUTIONS,
            recruit_no,
            action,
            _requirement_review_fingerprint(job),
            note,
            allowed_recruit_nos={
                int(record.get("recruit_no") or 0) for record in current_jobs
            },
        )
        _invalidate_dashboard_cache()
    return {"ok": True, "recruit_no": recruit_no, "action": action}


def _host_is_local(value: str) -> bool:
    host = value.strip().lower()
    if host.startswith("[") and "]" in host:
        hostname = host[1 : host.index("]")]
    else:
        hostname = host.rsplit(":", 1)[0] if ":" in host else host
    return hostname in LOCAL_HOSTS


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "AlbamonDashboard/1.1"

    def version_string(self) -> str:
        return self.server_version

    def _send_headers(
        self,
        status: HTTPStatus,
        content_type: str,
        length: int,
        *,
        cache: str = "no-store",
        content_encoding: str = "",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        self.send_header("Vary", "Accept-Encoding")
        if content_encoding:
            self.send_header("Content-Encoding", content_encoding)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://unpkg.com; img-src 'self' data: "
            "https://*.tile.openstreetmap.org; connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()

    def _send_bytes(
        self,
        data: bytes,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        content_type: str = "application/octet-stream",
        cache: str = "no-store",
        compress: bool = True,
    ) -> None:
        encoded, content_encoding = (
            encode_response_body(data, self.headers.get("Accept-Encoding", ""))
            if compress
            else (data, "")
        )
        self._send_headers(
            status,
            content_type,
            len(encoded),
            cache=cache,
            content_encoding=content_encoding,
        )
        self.wfile.write(encoded)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self._send_bytes(
            data,
            status=status,
            content_type="application/json; charset=utf-8",
        )

    def _reject_nonlocal_request(self) -> bool:
        if not _host_is_local(self.headers.get("Host", "")):
            self._send_json({"error": "Local dashboard requests only"}, HTTPStatus.FORBIDDEN)
            return True
        origin = self.headers.get("Origin")
        if origin and (urlparse(origin).hostname or "").lower() not in LOCAL_HOSTS:
            self._send_json({"error": "Cross-origin request rejected"}, HTTPStatus.FORBIDDEN)
            return True
        return False

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self._reject_nonlocal_request():
            return
        path = urlparse(self.path).path
        try:
            if path == "/api/dashboard":
                self._send_json(load_dashboard_payload())
                return
            if path == "/api/weekend-agencies":
                self._send_json(load_weekend_agency_payload())
                return
            operation_match = re.fullmatch(r"/api/operations/([0-9a-f]{20})", path)
            if operation_match:
                operation = OPERATIONS.get(operation_match.group(1))
                if operation is None:
                    self._send_json({"error": "Operation not found"}, HTTPStatus.NOT_FOUND)
                else:
                    self._send_json(operation)
                return
            detail_match = re.fullmatch(r"/api/jobs/(\d+)", path)
            if detail_match:
                detail = load_job_detail(int(detail_match.group(1)))
                if detail is None:
                    self._send_json({"error": "Job not found"}, HTTPStatus.NOT_FOUND)
                else:
                    self._send_json({"job": detail})
                return
            if path.startswith("/reports/"):
                name = path.removeprefix("/reports/")
                report_path = REPORT_NAMES.get(name)
                exploration_match = re.fullmatch(
                    r"weekend-agencies-explore/([0-9a-f]{20})\.(json|csv|md)",
                    name,
                )
                if exploration_match:
                    report_path = _exploration_prefix(
                        exploration_match.group(1)
                    ).with_suffix(f".{exploration_match.group(2)}")
                if report_path is None or not report_path.is_file():
                    self._send_json({"error": "Report not found"}, HTTPStatus.NOT_FOUND)
                    return
                content_type = mimetypes.guess_type(report_path.name)[0] or "text/plain"
                self._send_bytes(
                    report_path.read_bytes(),
                    content_type=f"{content_type}; charset=utf-8",
                )
                return
            static_path = STATIC_NAMES.get(path)
            if static_path is None or not static_path.is_file():
                self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(static_path.name)[0] or "text/plain"
            self._send_bytes(
                static_path.read_bytes(),
                content_type=f"{content_type}; charset=utf-8",
                cache="no-cache",
            )
        except DashboardDataError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self._reject_nonlocal_request():
            return
        path = urlparse(self.path).path
        if path not in {
            "/api/dispositions",
            "/api/reviews",
            "/api/weekend-agencies/search",
            "/api/origin/lookup",
            "/api/origin",
        }:
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        if self.headers.get_content_type() != "application/json":
            self._send_json({"error": "Expected application/json"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 4096:
            self._send_json({"error": "Request body must be between 1 and 4096 bytes"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Request body must contain a JSON object")
            if path == "/api/reviews":
                result = update_review_resolution(payload)
            elif path == "/api/dispositions":
                result = update_job_disposition(payload)
            elif path == "/api/weekend-agencies/search":
                result = start_weekend_agency_search(payload)
            elif path == "/api/origin/lookup":
                result = resolve_origin_address(payload)
            else:
                result = start_commute_origin_update(payload)
        except OperationCapacityError as exc:
            self._send_json(
                {"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE
            )
            return
        except (
            DashboardDataError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValueError,
            RuntimeError,
            OSError,
        ) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json(
            result,
            HTTPStatus.ACCEPTED
            if path in {"/api/weekend-agencies/search", "/api/origin"}
            else HTTPStatus.OK,
        )

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self._reject_nonlocal_request():
            return
        path = urlparse(self.path).path
        operation_match = re.fullmatch(r"/api/operations/([0-9a-f]{20})", path)
        if not operation_match:
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        operation = OPERATIONS.cancel(operation_match.group(1))
        if operation is None:
            self._send_json({"error": "Operation not found"}, HTTPStatus.NOT_FOUND)
            return
        self._send_json(operation)

    def log_message(self, format_string: str, *args: Any) -> None:
        message = re.sub(r"\s+", " ", format_string % args)
        print(f"[dashboard] {self.address_string()} {message}")


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_close(self) -> None:
        super().server_close()
        OPERATIONS.shutdown()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    server = DashboardServer((args.host, args.port), DashboardHandler)
    print(f"Albamon dashboard: http://{args.host}:{args.port}")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
