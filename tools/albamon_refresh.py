#!/usr/bin/env python3
"""Run the complete incremental Albamon refresh as one checkpointed workflow."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

try:
    from tools import albamon_application_messages as messages
    from tools import albamon_combined_jobs as combined
    from tools import albamon_transit as transit
    from tools.albamon_cache import DEFAULT_DETAIL_CACHE_PATH
except ModuleNotFoundError:  # Support direct execution from the repository root.
    import albamon_application_messages as messages
    import albamon_combined_jobs as combined
    import albamon_transit as transit
    from albamon_cache import DEFAULT_DETAIL_CACHE_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suit-no", type=int, default=combined.DEFAULT_SUIT_NO)
    parser.add_argument("--workers", type=int, default=combined.DEFAULT_WORKERS)
    parser.add_argument("--page-size", type=int, default=combined.DEFAULT_PAGE_SIZE)
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
        "--transit-cache", type=Path, default=transit.DEFAULT_OUTPUT
    )
    parser.add_argument(
        "--message-cache", type=Path, default=messages.DEFAULT_MESSAGE_CACHE
    )
    parser.add_argument(
        "--combined-only",
        action="store_true",
        help="Stop after job collection/detail screening; no Kakao key is required.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    timings: dict[str, float] = {}
    started = time.perf_counter()

    stage_started = time.perf_counter()
    combined_args = combined.parse_args(
        [
            "--suit-no",
            str(args.suit_no),
            "--workers",
            str(args.workers),
            "--page-size",
            str(args.page_size),
            "--detail-cache",
            str(args.detail_cache),
            "--output-prefix",
            str(args.combined_prefix),
        ]
    )
    report = combined.run(combined_args)
    timings["combined_seconds"] = round(time.perf_counter() - stage_started, 3)
    if args.combined_only:
        timings["total_seconds"] = round(time.perf_counter() - started, 3)
        print(json.dumps(timings, ensure_ascii=False))
        return 0

    api_key = os.environ.get("KAKAO_REST_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "Combined report completed, but KAKAO_REST_API_KEY is required "
            "before commute screening and the actionable application report."
        )

    stage_started = time.perf_counter()
    existing_transit = (
        json.loads(args.transit_cache.read_text(encoding="utf-8"))
        if args.transit_cache.exists()
        else None
    )
    transit_cache = transit.build_cache(
        report,
        api_key=api_key,
        workers=max(1, min(args.workers, 12)),
        existing_cache=existing_transit,
    )
    args.transit_cache.parent.mkdir(parents=True, exist_ok=True)
    temporary_transit = args.transit_cache.with_suffix(
        args.transit_cache.suffix + ".tmp"
    )
    temporary_transit.write_text(
        json.dumps(transit_cache, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_transit.replace(args.transit_cache)
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
    messages.write_json(
        args.application_prefix.with_suffix(".json"),
        args.combined_prefix.with_suffix(".json"),
        records,
    )
    messages.write_csv(args.application_prefix.with_suffix(".csv"), records)
    messages.write_markdown(args.application_prefix.with_suffix(".md"), records)
    messages.write_message_cache(args.message_cache, records)
    timings["application_report_seconds"] = round(
        time.perf_counter() - stage_started, 3
    )
    timings["total_seconds"] = round(time.perf_counter() - started, 3)

    print(
        json.dumps(
            {
                **timings,
                **message_metrics,
                "eligible_jobs": report["eligible_count"],
                "route_requests": transit_cache["route_request_count"],
                "route_cache_hits": transit_cache["route_cache_hit_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
