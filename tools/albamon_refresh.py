#!/usr/bin/env python3
"""Run the complete incremental Albamon refresh as one checkpointed workflow."""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime
from pathlib import Path

try:
    from tools import albamon_discord as discord
    from tools import albamon_application_messages as messages
    from tools import albamon_combined_jobs as combined
    from tools import albamon_transit as transit
    from tools.albamon_cache import DEFAULT_DETAIL_CACHE_PATH
    from tools.albamon_runtime import (
        AlreadyRunningError,
        atomic_write_text,
        exclusive_file_lock,
    )
except ModuleNotFoundError:  # Support direct execution from the repository root.
    import albamon_discord as discord
    import albamon_application_messages as messages
    import albamon_combined_jobs as combined
    import albamon_transit as transit
    from albamon_cache import DEFAULT_DETAIL_CACHE_PATH
    from albamon_runtime import (
        AlreadyRunningError,
        atomic_write_text,
        exclusive_file_lock,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suit-no", type=int, default=combined.DEFAULT_SUIT_NO)
    parser.add_argument("--workers", type=int, default=combined.DEFAULT_WORKERS)
    parser.add_argument("--page-size", type=int, default=combined.DEFAULT_PAGE_SIZE)
    parser.add_argument(
        "--availability-end-date",
        type=date.fromisoformat,
        default=combined.DEFAULT_AVAILABILITY_END_DATE,
        help="Last date on which a job may require work (inclusive).",
    )
    parser.add_argument(
        "--combined-prefix",
        type=Path,
        default=Path("artifacts/albamon/latest-combined"),
    )
    parser.add_argument(
        "--application-prefix",
        type=Path,
        default=Path("artifacts/albamon/latest-unified-report"),
    )
    parser.add_argument(
        "--detail-cache", type=Path, default=DEFAULT_DETAIL_CACHE_PATH
    )
    parser.add_argument(
        "--closing-refresh-hours",
        type=int,
        default=combined.DEFAULT_CLOSING_REFRESH_HOURS,
    )
    parser.add_argument(
        "--eligible-refresh-hours",
        type=int,
        default=combined.DEFAULT_ELIGIBLE_REFRESH_HOURS,
    )
    parser.add_argument(
        "--applied-refresh-hours",
        type=int,
        default=combined.DEFAULT_APPLIED_REFRESH_HOURS,
    )
    parser.add_argument(
        "--special-source-cache",
        type=Path,
        default=combined.DEFAULT_SPECIAL_SOURCE_CACHE_PATH,
    )
    parser.add_argument(
        "--special-source-cache-hours",
        type=int,
        default=combined.DEFAULT_SPECIAL_SOURCE_CACHE_HOURS,
    )
    parser.add_argument("--no-special-source-cache", action="store_true")
    parser.add_argument(
        "--exclude-late-night",
        action="store_true",
        help="Omit the separate station-corridor 19:00-02:00 evening lane.",
    )
    parser.add_argument(
        "--transit-cache", type=Path, default=transit.DEFAULT_OUTPUT
    )
    parser.add_argument(
        "--late-night-route-cache",
        type=Path,
        default=transit.DEFAULT_LATE_NIGHT_OUTPUT,
        help="Browser-captured Kakao shift-time and return-bike routes.",
    )
    parser.add_argument(
        "--message-cache", type=Path, default=messages.DEFAULT_MESSAGE_CACHE
    )
    parser.add_argument(
        "--route-cache-hours",
        type=int,
        default=transit.DEFAULT_ROUTE_CACHE_HOURS,
    )
    parser.add_argument(
        "--route-refresh-hour",
        type=int,
        default=transit.DEFAULT_ROUTE_REFRESH_HOUR,
        help="Refresh existing routes once daily after this local hour.",
    )
    parser.add_argument(
        "--kakao-env-file",
        type=Path,
        default=Path(".env"),
        help="Workspace dotenv file containing KAKAO_REST_API_KEY.",
    )
    parser.add_argument(
        "--kakao-quota-state",
        type=Path,
        default=transit.DEFAULT_KAKAO_QUOTA_STATE,
    )
    parser.add_argument(
        "--kakao-daily-limit",
        type=int,
        default=transit.DEFAULT_KAKAO_PUBLIC_TRANSIT_DAILY_LIMIT,
        help="Hard free-tier ceiling; default leaves a 100-call safety margin.",
    )
    parser.add_argument(
        "--performance-history",
        type=Path,
        default=Path("artifacts/albamon/cache/performance_history.json"),
    )
    parser.add_argument("--performance-history-limit", type=int, default=50)
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path("artifacts/albamon/cache/refresh.lock"),
        help="Prevent overlapping manual and scheduled refreshes.",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Notify Discord after the unified report is committed successfully.",
    )
    parser.add_argument(
        "--discord-state",
        type=Path,
        default=discord.DEFAULT_STATE_PATH,
    )
    parser.add_argument(
        "--webhook-env-file",
        type=Path,
        default=discord.DEFAULT_WEBHOOK_ENV_FILE,
    )
    parser.add_argument(
        "--combined-only",
        action="store_true",
        help="Stop after job collection/detail screening; no Kakao key is required.",
    )
    return parser.parse_args()


def append_performance_history(
    path: Path,
    entry: dict[str, object],
    *,
    limit: int = 50,
) -> None:
    """Persist a bounded, privacy-safe history of successful refresh runs."""

    if limit < 1:
        raise ValueError("performance history limit must be at least 1")
    payload: dict[str, object] = {"version": 1, "runs": []}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if loaded.get("version") == 1 and isinstance(loaded.get("runs"), list):
                payload = loaded
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    runs = [item for item in payload.get("runs", []) if isinstance(item, dict)]
    runs.append(entry)
    payload.update(
        {
            "version": 1,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "limit": limit,
            "runs": runs[-limit:],
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
    )


def record_refresh_performance(
    args: argparse.Namespace,
    report: dict[str, object],
    timings: dict[str, float],
    outcomes: dict[str, object],
) -> bool:
    try:
        append_performance_history(
            args.performance_history,
            {
                "completed_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "timings": dict(timings),
                "pipeline": dict(report.get("performance") or {}),
                "outcomes": outcomes,
            },
            limit=args.performance_history_limit,
        )
    except OSError:
        # Observability must not prevent an otherwise successful refresh and
        # downstream notification run.
        return False
    return True


def validate_refresh_outputs(
    report: dict[str, object],
    transit_cache: dict[str, object],
    unified_report: dict[str, object],
    *,
    observed_at: datetime | None = None,
) -> None:
    """Fail before notification if one pipeline stage produced mixed data."""

    observed_at = observed_at or datetime.now().astimezone()
    discord.validate_report_pair(report, unified_report)
    combined_records = [
        *(report.get("jobs") or []),
        *(report.get("logistics_jobs") or []),
    ]
    if int(report.get("eligible_count") or 0) != len(combined_records):
        raise ValueError("Combined eligible count does not match its job records")
    if any(record.get("work_term_ended") for record in combined_records):
        raise ValueError("An ended work term survived into the eligible report")
    expired_eligible = [
        int(record["recruit_no"])
        for record in combined_records
        if combined.record_work_term_has_expired(
            record, observed_at=observed_at
        )[0]
    ]
    if expired_eligible:
        raise ValueError(
            "An expired work term survived into the eligible report: "
            + ", ".join(str(recruit_no) for recruit_no in expired_eligible)
        )
    expired_audit = [
        int(record["recruit_no"])
        for record in [
            *(report.get("excluded_jobs") or []),
            *(report.get("pre_detail_excluded_jobs") or []),
        ]
        if combined.record_work_term_has_expired(
            record, observed_at=observed_at
        )[0]
    ]
    if expired_audit:
        raise ValueError(
            "Expired jobs were not flushed from the latest audit report: "
            + ", ".join(str(recruit_no) for recruit_no in expired_audit)
        )
    if any(record.get("availability_compatible") is False for record in combined_records):
        raise ValueError("A job outside the availability window survived eligibility")
    availability_end = str(report.get("availability_end_date") or "")
    if not availability_end:
        raise ValueError("Combined report is missing its availability deadline")
    if any(
        str(record.get("availability_end_date") or "") != availability_end
        for record in combined_records
    ):
        raise ValueError("Eligible jobs do not share the report availability deadline")
    configured_sources = report.get("configured_source_names")
    if configured_sources is not None:
        source_counts = report.get("source_counts") or {}
        parity = report.get("source_parity") or {}
        if not isinstance(configured_sources, list) or not isinstance(
            source_counts, dict
        ):
            raise ValueError("Combined source configuration has an invalid shape")
        if set(configured_sources) != set(source_counts):
            raise ValueError("Configured source names do not match collected source counts")
        parity_counts = parity.get("exclusive_counts") if isinstance(parity, dict) else None
        if not isinstance(parity_counts, dict) or set(parity_counts) != set(
            configured_sources
        ):
            raise ValueError("Source parity telemetry is incomplete")
    combined_ids = {int(record["recruit_no"]) for record in combined_records}
    transit_jobs = transit_cache.get("jobs") or {}
    if not isinstance(transit_jobs, dict):
        raise ValueError("Transit cache jobs must be an object")
    try:
        transit_ids = {int(recruit_no) for recruit_no in transit_jobs}
    except (TypeError, ValueError) as exc:
        raise ValueError("Transit cache contains an invalid recruit number") from exc
    if transit_ids != combined_ids:
        raise ValueError("Transit cache and combined report job IDs do not match")
    for record in combined_records:
        if not (
            record.get("remote_job")
            or "remote_short_term" in (record.get("eligibility_lanes") or [])
        ):
            continue
        route = transit_jobs.get(str(record["recruit_no"])) or {}
        if (
            route.get("route_status") != "not_required"
            or route.get("transit_minutes") != 0
            or route.get("distance_excluded") is not False
        ):
            raise ValueError("Remote job did not receive the no-commute transit result")


def run_refresh(args: argparse.Namespace) -> int:
    if args.combined_only and args.notify:
        raise ValueError("--notify cannot be combined with --combined-only")
    if args.route_cache_hours < 1:
        raise ValueError("--route-cache-hours must be at least 1")
    if not 0 <= args.route_refresh_hour <= 23:
        raise ValueError("--route-refresh-hour must be between 0 and 23")
    if args.performance_history_limit < 1:
        raise ValueError("--performance-history-limit must be at least 1")
    if not 1 <= args.kakao_daily_limit <= transit.KAKAO_PUBLIC_TRANSIT_FREE_DAILY_QUOTA:
        raise ValueError(
            "--kakao-daily-limit must stay within Kakao's documented free quota"
        )
    timings: dict[str, float] = {}
    started = time.perf_counter()

    stage_started = time.perf_counter()
    combined_argv = [
        "--suit-no",
        str(args.suit_no),
        "--workers",
        str(args.workers),
        "--page-size",
        str(args.page_size),
        "--availability-end-date",
        args.availability_end_date.isoformat(),
        "--detail-cache",
        str(args.detail_cache),
        "--closing-refresh-hours",
        str(args.closing_refresh_hours),
        "--eligible-refresh-hours",
        str(args.eligible_refresh_hours),
        "--applied-refresh-hours",
        str(args.applied_refresh_hours),
        "--special-source-cache",
        str(args.special_source_cache),
        "--special-source-cache-hours",
        str(args.special_source_cache_hours),
        "--output-prefix",
        str(args.combined_prefix),
    ]
    if args.no_special_source_cache:
        combined_argv.append("--no-special-source-cache")
    if args.exclude_late_night:
        combined_argv.append("--exclude-late-night")
    combined_args = combined.parse_args(combined_argv)
    report = combined.run(combined_args)
    timings["combined_seconds"] = round(time.perf_counter() - stage_started, 3)
    if args.combined_only:
        timings["total_seconds"] = round(time.perf_counter() - started, 3)
        history_saved = record_refresh_performance(
            args,
            report,
            timings,
            {"eligible_jobs": report["eligible_count"], "combined_only": True},
        )
        print(
            json.dumps(
                {**timings, "performance_history_saved": history_saved},
                ensure_ascii=False,
            )
        )
        return 0

    stage_started = time.perf_counter()
    existing_transit = (
        json.loads(args.transit_cache.read_text(encoding="utf-8"))
        if args.transit_cache.exists()
        else None
    )
    late_night_route_cache = transit.load_late_night_route_cache(
        args.late_night_route_cache
    )
    existing_transit = transit.merge_late_night_routes(
        report,
        existing_transit,
        late_night_route_cache,
    )
    api_key = transit.env_file_value(
        args.kakao_env_file, "KAKAO_REST_API_KEY"
    )
    quota_budget = (
        transit.KakaoQuotaBudget(
            args.kakao_quota_state,
            daily_limit=args.kakao_daily_limit,
        )
        if api_key
        else None
    )
    try:
        transit_cache = transit.build_cache(
            report,
            api_key=api_key,
            workers=max(1, min(args.workers, 12)),
            existing_cache=existing_transit,
            max_age_hours=args.route_cache_hours,
            refresh_hour=args.route_refresh_hour,
            reuse_stale_verified_routes=not bool(api_key),
            defer_unverified_routes=True,
            quota_budget=quota_budget,
        )
    except ValueError as exc:
        if "KAKAO_REST_API_KEY" not in str(exc):
            raise
        raise SystemExit(
            "Combined report completed, but one or more routes need a fresh "
            "Kakao Maps web-UI pass before reporting and notifications."
        ) from exc
    args.transit_cache.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        args.transit_cache,
        json.dumps(transit_cache, ensure_ascii=False, indent=2) + "\n",
    )
    timings["transit_seconds"] = round(time.perf_counter() - stage_started, 3)

    stage_started = time.perf_counter()
    message_metrics: dict[str, int] = {}
    records = messages.build_records(
        report,
        messages.load_dispositions(messages.DEFAULT_DISPOSITIONS),
        messages.load_transit_cache(args.transit_cache),
        messages.load_message_cache(args.message_cache),
        message_metrics,
    )
    args.application_prefix.parent.mkdir(parents=True, exist_ok=True)
    unified_report = messages.write_json(
        args.application_prefix.with_suffix(".json"),
        args.combined_prefix.with_suffix(".json"),
        records,
        source_report=report,
    )
    messages.write_csv(args.application_prefix.with_suffix(".csv"), records)
    messages.write_markdown(args.application_prefix.with_suffix(".md"), records)
    messages.write_message_cache(args.message_cache, records)
    validate_refresh_outputs(report, transit_cache, unified_report)
    timings["application_report_seconds"] = round(
        time.perf_counter() - stage_started, 3
    )
    timings["total_seconds"] = round(time.perf_counter() - started, 3)

    outcomes = {
        **message_metrics,
        "eligible_jobs": report["eligible_count"],
        "eligible_evening_jobs": report.get("eligible_late_night_count", 0),
        "eligible_remote_jobs": report.get("eligible_remote_count", 0),
        "removed_or_excluded_jobs": report.get("excluded_count", 0),
        "closed_jobs_removed": report.get("closed_detail_listing_count", 0),
        "source_exclusive_counts": (
            (report.get("source_parity") or {}).get("exclusive_counts") or {}
        ),
        "route_requests": transit_cache["route_request_count"],
        "route_cache_hits": transit_cache["route_cache_hit_count"],
        "route_stale_reuses": transit_cache["route_stale_reuse_count"],
        "route_deferred": transit_cache["route_deferred_count"],
        "webui_fallback_required": transit_cache[
            "webui_fallback_required_count"
        ],
        "integrity_checks_passed": True,
        **(
            {"kakao_public_transit_quota": transit_cache["kakao_public_transit_quota"]}
            if transit_cache.get("kakao_public_transit_quota")
            else {}
        ),
    }
    if args.notify:
        webhook_url = discord.resolve_discord_webhook(args.webhook_env_file)
        try:
            notification_metrics = discord.notify_new_jobs(
                report,
                unified_report,
                state_path=args.discord_state,
                sender=lambda message: discord.post_discord_message(
                    webhook_url, message
                ),
            )
        except AlreadyRunningError:
            notification_metrics = {"skipped": "notification_already_running"}
        outcomes["discord_notifications"] = notification_metrics
    outcomes["performance_history_saved"] = record_refresh_performance(
        args, report, timings, outcomes
    )

    print(
        json.dumps(
            {
                **timings,
                **outcomes,
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    args = parse_args()
    try:
        with exclusive_file_lock(args.lock_file):
            return run_refresh(args)
    except AlreadyRunningError:
        print(
            json.dumps(
                {"skipped": "refresh_already_running"},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
