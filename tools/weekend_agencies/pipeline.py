"""Orchestration and integrity checks for the isolated agency workflow."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

try:
    from tools.albamon_runtime import atomic_write_text
except ModuleNotFoundError:  # Direct CLI execution via tools/.
    from albamon_runtime import atomic_write_text

from .config import (
    MIN_ACTUAL_HOURLY_WON,
    OUTPUT_PREFIX,
    SOURCE_CACHE_PATH,
    SEARCH_DATES,
    SEARCH_END,
    SEARCH_START,
)
from .http import HttpClient
from .models import JobOffer
from .models import PayBreakdown
from .normalize import aggregate_offers, offer_is_actionable
from .report import write_report
from .routing import attach_routes
from .sources import EumplusSource, HupluxSource, WithYouSource


KOREA_TIMEZONE = ZoneInfo("Asia/Seoul")


class PipelineError(RuntimeError):
    pass


SOURCE_SNAPSHOT_MAX_AGE_HOURS = 8


def default_sources() -> tuple[object, ...]:
    return (WithYouSource(), HupluxSource(), EumplusSource())


def _fingerprint(job: dict[str, Any]) -> str:
    material = json.dumps(
        {
            "group_id": job["group_id"],
            "best_actual_hourly_won": job["best_actual_hourly_won"],
            "best_weekly_allowance_hourly_won": job[
                "best_weekly_allowance_hourly_won"
            ],
            "agencies": job["agencies"],
            "highest_paying_agencies": job.get("highest_paying_agencies") or [],
            "transit_minutes": job["transit_minutes"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _previous_jobs(prefix: Path) -> dict[str, dict[str, Any]]:
    path = prefix.with_suffix(".json")
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(item["group_id"]): item
        for item in payload.get("jobs") or []
        if isinstance(item, dict) and item.get("group_id")
    }


def _offer_from_dict(payload: dict[str, Any]) -> JobOffer:
    pay_payload = dict(payload.get("pay") or {})
    pay_payload["warnings"] = tuple(pay_payload.get("warnings") or ())
    fields = {
        key: value
        for key, value in payload.items()
        if key not in {"pay", "work_date", "shift"}
    }
    return JobOffer(
        **fields,
        work_date=date.fromisoformat(str(payload["work_date"])),
        pay=PayBreakdown(**pay_payload),
    )


def _load_source_snapshots(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "sources": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "sources": {}}
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return {"version": 1, "sources": {}}
    if not isinstance(payload.get("sources"), dict):
        payload["sources"] = {}
    return payload


def _snapshot_offers(
    snapshots: dict[str, Any], source_name: str, *, now: datetime
) -> tuple[list[JobOffer], str | None]:
    entry = (snapshots.get("sources") or {}).get(source_name)
    if not isinstance(entry, dict) or not isinstance(entry.get("offers"), list):
        return [], None
    try:
        checked_at = datetime.fromisoformat(str(entry.get("checked_at") or ""))
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=KOREA_TIMEZONE)
    except ValueError:
        return [], None
    if now - checked_at > timedelta(hours=SOURCE_SNAPSHOT_MAX_AGE_HOURS):
        return [], None
    try:
        return (
            [_offer_from_dict(item) for item in entry["offers"] if isinstance(item, dict)],
            checked_at.isoformat(timespec="seconds"),
        )
    except (TypeError, ValueError):
        return [], None


def _bootstrap_snapshot_from_previous(
    prefix: Path, source_name: str, *, now: datetime
) -> tuple[list[JobOffer], str | None]:
    report_path = prefix.with_suffix(".json")
    if not report_path.exists():
        return [], None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], None
    source_state = (report.get("sources") or {}).get(source_name) or {}
    origin_text = str(
        source_state.get("snapshot_checked_at")
        or report.get("generated_at")
        or ""
    )
    try:
        origin = datetime.fromisoformat(origin_text)
        if origin.tzinfo is None:
            origin = origin.replace(tzinfo=KOREA_TIMEZONE)
    except ValueError:
        return [], None
    if now - origin > timedelta(hours=SOURCE_SNAPSHOT_MAX_AGE_HOURS):
        return [], None
    offers: list[JobOffer] = []
    for job in report.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        for payload in job.get("offers") or []:
            if isinstance(payload, dict) and payload.get("agency") == source_name:
                try:
                    offers.append(_offer_from_dict(payload))
                except (TypeError, ValueError):
                    continue
    return offers, origin.isoformat(timespec="seconds")


def validate_report(report: dict[str, Any]) -> None:
    jobs = report.get("jobs")
    if not isinstance(jobs, list):
        raise PipelineError("report jobs must be a list")
    group_ids: set[str] = set()
    for expected_rank, job in enumerate(jobs, start=1):
        if job.get("rank") != expected_rank:
            raise PipelineError("report ranks are not contiguous")
        group_id = str(job.get("group_id") or "")
        if not group_id or group_id in group_ids:
            raise PipelineError("report contains a missing or duplicate group ID")
        group_ids.add(group_id)
        work_date = date.fromisoformat(str(job["work_date"]))
        if work_date not in SEARCH_DATES or work_date.weekday() < 5:
            raise PipelineError("report contains a job outside the weekend window")
        if int(job["best_actual_hourly_won"]) < MIN_ACTUAL_HOURLY_WON:
            raise PipelineError("report contains a below-threshold actual wage")
        offer_pays = [
            offer.get("pay", {}).get("actual_hourly_won")
            for offer in job.get("offers") or []
        ]
        if job["best_actual_hourly_won"] != max(offer_pays):
            raise PipelineError("best agency pay does not match the retained offers")
    expected_order = sorted(
        jobs,
        key=lambda item: (
            -int(item["best_actual_hourly_won"]),
            int(item["transit_minutes"])
            if isinstance(item.get("transit_minutes"), int)
            else 10_000,
            str(item["work_date"]),
            str(item["employer"]),
            str(item["shift"]),
        ),
    )
    if [item["group_id"] for item in jobs] != [
        item["group_id"] for item in expected_order
    ]:
        raise PipelineError("report is not sorted by actual pay then commute")


def _collect_sources(
    sources: Iterable[object],
    *,
    client: HttpClient,
    output_prefix: Path,
    source_cache_path: Path,
    now: datetime,
) -> tuple[list[JobOffer], dict[str, dict[str, Any]]]:
    source_rows: list[JobOffer] = []
    source_metrics: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    source_snapshots = _load_source_snapshots(source_cache_path)
    snapshots_changed = False
    for source in sources:
        source_name = str(getattr(source, "name", type(source).__name__))
        source_started = time.monotonic()
        try:
            offers = list(source.fetch(client))
        except Exception as exc:
            offers, snapshot_checked_at = _snapshot_offers(
                source_snapshots, source_name, now=now
            )
            if not offers:
                offers, snapshot_checked_at = _bootstrap_snapshot_from_previous(
                    output_prefix, source_name, now=now
                )
            if not offers:
                failures[source_name] = type(exc).__name__
                continue
            offers = [
                replace(
                    item,
                    metadata={**item.metadata, "source_snapshot_stale": True},
                )
                for item in offers
            ]
            source_metrics[source_name] = {
                "status": "stale_cache",
                "fetched_offer_count": len(offers),
                "elapsed_seconds": round(time.monotonic() - source_started, 3),
                "failure_type": type(exc).__name__,
                "snapshot_max_age_hours": SOURCE_SNAPSHOT_MAX_AGE_HOURS,
                "snapshot_checked_at": snapshot_checked_at,
            }
            source_snapshots.setdefault("sources", {})[source_name] = {
                "checked_at": snapshot_checked_at,
                "offers": [item.as_dict() for item in offers],
            }
            snapshots_changed = True
            source_rows.extend(offers)
            continue
        source_rows.extend(offers)
        source_metrics[source_name] = {
            "status": "live",
            "fetched_offer_count": len(offers),
            "elapsed_seconds": round(time.monotonic() - source_started, 3),
        }
        source_snapshots.setdefault("sources", {})[source_name] = {
            "checked_at": now.isoformat(timespec="seconds"),
            "offers": [item.as_dict() for item in offers],
        }
        snapshots_changed = True

    if failures:
        names = ", ".join(sorted(failures))
        raise PipelineError(
            f"source refresh failed for {names}; previous complete report was preserved"
        )

    if snapshots_changed:
        source_snapshots["updated_at"] = now.isoformat(timespec="seconds")
        atomic_write_text(
            source_cache_path,
            json.dumps(source_snapshots, ensure_ascii=False, separators=(",", ":"))
            + "\n",
        )
    return source_rows, source_metrics


def _build_report(
    *,
    source_rows: list[JobOffer],
    jobs: list[dict[str, object]],
    source_metrics: dict[str, dict[str, Any]],
    routing_metrics: dict[str, Any],
    output_prefix: Path,
    now: datetime,
    elapsed_seconds: float,
) -> dict[str, Any]:
    previous = _previous_jobs(output_prefix)
    current_by_id = {str(job["group_id"]): job for job in jobs}
    current_ids = set(current_by_id)
    new_ids = sorted(current_ids - previous.keys())
    changed_ids = sorted(
        group_id
        for group_id in current_ids & previous.keys()
        if _fingerprint(current_by_id[group_id]) != _fingerprint(previous[group_id])
    )
    removed_ids = sorted(previous.keys() - current_ids)
    generated_at = now.isoformat(timespec="seconds")
    run_material = f"{generated_at}|{len(source_rows)}|{','.join(sorted(current_ids))}"
    report: dict[str, Any] = {
        "version": 1,
        "run_id": hashlib.sha256(run_material.encode("utf-8")).hexdigest()[:20],
        "generated_at": generated_at,
        "separate_from_albamon": True,
        "policy": {
            "search_start": SEARCH_START.isoformat(),
            "search_end": SEARCH_END.isoformat(),
            "weekend_dates": [value.isoformat() for value in SEARCH_DATES],
            "minimum_actual_hourly_won": MIN_ACTUAL_HOURLY_WON,
            "pay_filter_field": "actual_hourly_won",
            "weekly_allowance_used_for_filter_or_rank": False,
            "sort": ["actual_hourly_won_desc", "verified_transit_minutes_asc"],
            "origin": "서울특별시 송파구 잠실동 189-4",
        },
        "counts": {
            "fetched_offers": len(source_rows),
            "eligible_offers": sum(
                len(job.get("offers") or []) for job in jobs
            ),
            "jobs": len(jobs),
            "cross_agency_duplicates": sum(
                bool(job["cross_agency_duplicate"]) for job in jobs
            ),
            "new": len(new_ids),
            "changed": len(changed_ids),
            "removed": len(removed_ids),
        },
        "new_group_ids": new_ids,
        "changed_group_ids": changed_ids,
        "removed_group_ids": removed_ids,
        "sources": source_metrics,
        "all_sources_live": all(
            item.get("status") == "live" for item in source_metrics.values()
        ),
        "routing": routing_metrics,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "integrity_checks_passed": True,
        "jobs": jobs,
    }
    validate_report(report)
    return report


def run_pipeline(
    *,
    api_key: str,
    output_prefix: Path = OUTPUT_PREFIX,
    sources: Iterable[object] | None = None,
    client: HttpClient | None = None,
    source_cache_path: Path = SOURCE_CACHE_PATH,
    route_enricher: Any = attach_routes,
    now: datetime | None = None,
    write_outputs: bool = True,
) -> tuple[dict[str, Any], dict[str, str]]:
    now = now or datetime.now(KOREA_TIMEZONE)
    if now.date() > SEARCH_END:
        raise PipelineError("search_window_ended")
    started = time.monotonic()
    source_rows, source_metrics = _collect_sources(
        sources or default_sources(),
        client=client or HttpClient(),
        output_prefix=output_prefix,
        source_cache_path=source_cache_path,
        now=now,
    )

    actionable = [offer for offer in source_rows if offer_is_actionable(offer)]
    routed, routing_metrics = route_enricher(actionable, api_key=api_key)
    jobs = aggregate_offers(routed)
    report = _build_report(
        source_rows=source_rows,
        jobs=jobs,
        source_metrics=source_metrics,
        routing_metrics=routing_metrics,
        output_prefix=output_prefix,
        now=now,
        elapsed_seconds=time.monotonic() - started,
    )
    paths = write_report(output_prefix, report) if write_outputs else {}
    return report, paths
