#!/usr/bin/env python3
"""Serve a local decision dashboard from the latest Albamon reports.

The dashboard deliberately treats the generated JSON/CSV/Markdown artifacts as
the source of truth. It adds a read-only view over those artifacts plus a small,
reversible disposition editor for defer/do-not-apply choices.
"""

from __future__ import annotations

import argparse
import gzip
import json
import mimetypes
import re
import threading
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from tools import albamon_application_messages as application_messages
    from tools.albamon_pareto import annotate_pareto_frontier
    from tools.albamon_runtime import atomic_write_text
    from tools.albamon_transit import ORIGIN, cache_origin_matches
    from tools.albamon_discord import notification_identity, validate_report_pair
    from tools.weekend_agencies.hotel_digest import aggregate_hotels
    from tools.weekend_agencies.pareto import annotate_hotel_frontiers
except ModuleNotFoundError:  # Support ``python3 tools/albamon_dashboard.py``.
    import albamon_application_messages as application_messages
    from albamon_pareto import annotate_pareto_frontier
    from albamon_runtime import atomic_write_text
    from albamon_transit import ORIGIN, cache_origin_matches
    from albamon_discord import notification_identity, validate_report_pair
    from weekend_agencies.hotel_digest import aggregate_hotels
    from weekend_agencies.pareto import annotate_hotel_frontiers


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
COMBINED_REPORT = ROOT / "artifacts/albamon/latest-combined.json"
UNIFIED_PREFIX = ROOT / "artifacts/albamon/latest-unified-report"
UNIFIED_REPORT = UNIFIED_PREFIX.with_suffix(".json")
TRANSIT_CACHE = ROOT / "tools/albamon_transit_cache.json"
MESSAGE_CACHE = ROOT / "artifacts/albamon/cache/application_messages.json"
DISCORD_STATE = ROOT / "artifacts/albamon/cache/discord_notifications.json"
DISPOSITIONS = ROOT / "tools/albamon_application_dispositions.json"
WEEKEND_AGENCY_REPORT = ROOT / "artifacts/weekend-agencies/latest.json"
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
    "/styles.css": WEB_ROOT / "styles.css",
}
ALLOWED_DISPOSITIONS = {"apply", "maybe_later", "do_not_apply"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
WRITE_LOCK = threading.Lock()
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
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the compact initial payload used by the table and map."""

    payload, _details = build_dashboard_snapshot(
        combined_report, unified_report, discord_state, now=now
    )
    return payload


def build_dashboard_snapshot(
    combined_report: dict[str, Any],
    unified_report: dict[str, Any],
    discord_state: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    route_origin_current: bool = True,
) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """Join decision, location, deadline, and notification data for the UI."""

    observed_at = now or datetime.now().astimezone()
    if observed_at.tzinfo is None:
        observed_at = observed_at.astimezone()
    state = discord_state or {}
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
        jobs.append(job)

    pareto_frontier_count = annotate_pareto_frontier(jobs)
    actionable = [job for job in jobs if job.get("application_disposition") == "apply"]
    summary = {
        "new_actionable_count": sum(bool(job["is_new"]) for job in actionable),
        "actionable_count": len(actionable),
        "closing_soon_count": sum(bool(job["is_closing_soon"]) for job in actionable),
        "needs_review_count": sum(
            job.get("send_readiness") == "conditional_on_requirement"
            or job.get("application_disposition") in {"maybe_later", "do_not_apply"}
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
        "origin": ORIGIN,
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
        payload, details = build_dashboard_snapshot(
            combined,
            unified,
            state,
            route_origin_current=cache_origin_matches(transit_cache),
        )
        _CACHE_SIGNATURE = signature
        _CACHE_PAYLOAD = payload
        _CACHE_DETAILS = details
        return payload, details


def load_dashboard_payload() -> dict[str, Any]:
    payload, _details = load_dashboard_snapshot()
    return payload


def build_weekend_agency_payload(report: dict[str, Any]) -> dict[str, Any]:
    """Build the lazy-loaded, hotel-level dashboard projection."""

    jobs = report.get("jobs") or []
    if not isinstance(jobs, list):
        raise DashboardDataError("Weekend agency report jobs must be a list")
    hotels = aggregate_hotels(job for job in jobs if isinstance(job, dict))
    frontier_counts = annotate_hotel_frontiers(hotels)
    sources = report.get("sources") if isinstance(report.get("sources"), dict) else {}
    policy = report.get("policy") if isinstance(report.get("policy"), dict) else {}
    return {
        "payload_version": 1,
        "run_id": str(report.get("run_id") or ""),
        "generated_at": str(report.get("generated_at") or ""),
        "all_sources_live": bool(report.get("all_sources_live")),
        "integrity_checks_passed": bool(report.get("integrity_checks_passed")),
        "policy": policy,
        "sources": sources,
        "summary": {
            "hotel_count": len(hotels),
            "job_count": len(jobs),
            "cross_agency_duplicate_count": int(
                (report.get("counts") or {}).get("cross_agency_duplicates") or 0
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
            {"label": "Markdown", "url": "/reports/weekend-agencies-latest.md"},
            {"label": "CSV", "url": "/reports/weekend-agencies-latest.csv"},
            {"label": "JSON", "url": "/reports/weekend-agencies-latest.json"},
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
        if urlparse(self.path).path != "/api/dispositions":
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
            result = update_job_disposition(payload)
        except (DashboardDataError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json(result)

    def log_message(self, format_string: str, *args: Any) -> None:
        message = re.sub(r"\s+", " ", format_string % args)
        print(f"[dashboard] {self.address_string()} {message}")


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True


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
