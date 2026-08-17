#!/usr/bin/env python3
"""Refresh the separate 2026-08-29 through 2026-09-06 weekend report."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from tools.albamon_runtime import (
        AlreadyRunningError,
        atomic_write_text,
        exclusive_file_lock,
    )
    from tools.weekend_agencies.config import LOCK_PATH, OUTPUT_PREFIX, SEARCH_END
    from tools.weekend_agencies.notifications import (
        DEFAULT_STATE_PATH,
        HotelierNotificationError,
        notify_report,
        post_discord_message,
        resolve_hotelier_webhook,
    )
    from tools.weekend_agencies.pipeline import PipelineError, run_pipeline
    from tools.weekend_agencies.routing import read_api_key
except ModuleNotFoundError:  # Direct execution from repository root.
    from albamon_runtime import AlreadyRunningError, atomic_write_text, exclusive_file_lock
    from weekend_agencies.config import LOCK_PATH, OUTPUT_PREFIX, SEARCH_END
    from weekend_agencies.notifications import (
        DEFAULT_STATE_PATH,
        HotelierNotificationError,
        notify_report,
        post_discord_message,
        resolve_hotelier_webhook,
    )
    from weekend_agencies.pipeline import PipelineError, run_pipeline
    from weekend_agencies.routing import read_api_key


KOREA_TIMEZONE = ZoneInfo("Asia/Seoul")
LAST_RUN_PATH = Path("artifacts/weekend-agencies/cache/last_run.json")


def emit_status(payload: dict[str, object], *, checkpoint: bool = True) -> None:
    rendered = json.dumps(payload, ensure_ascii=False)
    if checkpoint:
        atomic_write_text(LAST_RUN_PATH, rendered + "\n")
    print(rendered, flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-prefix", type=Path, default=OUTPUT_PREFIX)
    parser.add_argument("--lock-file", type=Path, default=LOCK_PATH)
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send new or changed hotel shifts to DISCORD_WEBHOOK_URL_HOTELIER.",
    )
    parser.add_argument(
        "--notification-state", type=Path, default=DEFAULT_STATE_PATH
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    now = datetime.now(KOREA_TIMEZONE)
    report: dict[str, object] | None = None
    notification_metrics: dict[str, int | bool] | None = None
    if now.date() > SEARCH_END:
        emit_status(
            {
                "status": "search_window_ended",
                "search_end": SEARCH_END.isoformat(),
                "checked_at": now.isoformat(timespec="seconds"),
            }
        )
        return 0
    try:
        with exclusive_file_lock(args.lock_file):
            report, paths = run_pipeline(
                api_key=read_api_key(args.env_file),
                output_prefix=args.output_prefix,
                now=now,
            )
            if args.notify:
                webhook_url = resolve_hotelier_webhook(args.env_file)
                notification_metrics = notify_report(
                    report,
                    state_path=args.notification_state,
                    sender=lambda message: post_discord_message(
                        webhook_url, message
                    ),
                )
    except AlreadyRunningError:
        emit_status({"status": "refresh_already_running"}, checkpoint=False)
        return 0
    except PipelineError as exc:
        emit_status(
            {
                "status": "failed",
                "error": str(exc),
                "checked_at": now.isoformat(timespec="seconds"),
            }
        )
        return 2
    except HotelierNotificationError as exc:
        emit_status(
            {
                "status": "notification_failed",
                "error": str(exc),
                "run_id": report.get("run_id") if report else None,
                "jobs": (
                    (report.get("counts") or {}).get("jobs") if report else None
                ),
                "checked_at": now.isoformat(timespec="seconds"),
            }
        )
        return 3
    payload: dict[str, object] = {
        "status": "ok",
        "run_id": report["run_id"],
        "jobs": report["counts"]["jobs"],
        "new": report["counts"]["new"],
        "changed": report["counts"]["changed"],
        "removed": report["counts"]["removed"],
        "cross_agency_duplicates": report["counts"][
            "cross_agency_duplicates"
        ],
        "kakao_calls": report["routing"]["kakao_calls_this_run"],
        "unresolved_locations": report["routing"][
            "unresolved_location_count"
        ],
        "integrity_checks_passed": report["integrity_checks_passed"],
        "all_sources_live": report["all_sources_live"],
        "stale_sources": [
            name
            for name, state in report["sources"].items()
            if state.get("status") != "live"
        ],
        "outputs": paths,
        "checked_at": now.isoformat(timespec="seconds"),
    }
    if notification_metrics is not None:
        payload["discord_notifications"] = notification_metrics
    emit_status(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
