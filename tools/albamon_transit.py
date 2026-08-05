#!/usr/bin/env python3
"""Measure Albamon job commutes from Jamsilsaenae by bus/train only.

Kakao's documented public-transit endpoint requires an application REST key.
Set ``KAKAO_REST_API_KEY`` before running. The key is used only in request
headers and is never written to the cache or report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from tools.albamon_jobs import fetch_job_detail
except ModuleNotFoundError:
    from albamon_jobs import fetch_job_detail


KAKAO_TRANSIT_URL = "https://dapi.kakao.com/v2/routing/publictraffic"
KAKAO_ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"
DEFAULT_INPUT = Path("artifacts/albamon/latest-combined.json")
DEFAULT_OUTPUT = Path("tools/albamon_transit_cache.json")
DEFAULT_WORKERS = 6
ORIGIN = {
    "name": "잠실새내역",
    "latitude": 37.5115,
    "longitude": 127.0867,
}
MAX_TRANSIT_MINUTES = 50
DEFAULT_ROUTE_CACHE_HOURS = 24


def fastest_route(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return the quickest public-transit route from a Kakao response."""

    routes = payload.get("routes")
    if not isinstance(routes, list):
        return None
    valid = [
        route
        for route in routes
        if isinstance(route, dict)
        and isinstance(route.get("properties"), dict)
        and isinstance(route["properties"].get("totalTime"), int)
    ]
    return min(valid, key=lambda route: route["properties"]["totalTime"]) if valid else None


def route_vehicle_summary(route: dict[str, Any]) -> list[str]:
    vehicles: list[str] = []
    for step in route.get("steps") or []:
        for vehicle in (step.get("properties") or {}).get("vehicles") or []:
            name = str(vehicle.get("name") or "").strip()
            if name and name not in vehicles:
                vehicles.append(name)
    return vehicles


def summarize_route(
    payload: dict[str, Any], *, max_minutes: int = MAX_TRANSIT_MINUTES
) -> dict[str, Any]:
    """Normalize a Kakao route response and apply the strict commute cutoff."""

    route = fastest_route(payload)
    if route is None:
        return {
            "route_status": "unverified",
            "distance_excluded": True,
            "distance_exclusion_reason": "대중교통 경로를 확인할 수 없음",
        }
    properties = route["properties"]
    seconds = int(properties["totalTime"])
    minutes = math.ceil(seconds / 60)
    # The report rounds upward to whole minutes. Requiring the displayed route
    # to be strictly below the cutoff keeps a 50-minute route out and avoids
    # showing an apparently disallowed value in the eligible sections.
    excluded = minutes >= max_minutes
    return {
        "route_status": "verified",
        "transit_seconds": seconds,
        "transit_minutes": minutes,
        "transit_type": str(properties.get("type") or ""),
        "transfers": properties.get("transfers"),
        "fare_won": (properties.get("fare") or {}).get("value"),
        "vehicles": route_vehicle_summary(route),
        "landing_url": (payload.get("properties") or {}).get("landingURL") or "",
        "distance_excluded": excluded,
        "distance_exclusion_reason": (
            f"잠실새내역에서 대중교통 최단 {minutes}분으로 50분 미만 기준 미충족"
            if excluded
            else ""
        ),
    }


def read_kakao_route(
    *, api_key: str, latitude: float, longitude: float, timeout: int = 15
) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {
            "start_x": ORIGIN["longitude"],
            "start_y": ORIGIN["latitude"],
            "s_name": ORIGIN["name"],
            "end_x": longitude,
            "end_y": latitude,
            "e_name": "근무지",
            "input_coord": "WGS84",
            "output_coord": "WGS84",
        }
    )
    request = urllib.request.Request(
        f"{KAKAO_TRANSIT_URL}?{query}",
        headers={
            "Authorization": f"KakaoAK {api_key}",
            "KA": (
                "sdk/1.0 os/javascript lang/ko-KR device/none "
                "origin/https%3A%2F%2Fmap.kakao.com"
            ),
            "Origin": "https://map.kakao.com",
            "Referer": "https://map.kakao.com/",
            "User-Agent": "JobScout/1.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.4)
    raise RuntimeError(f"Kakao transit lookup failed: {last_error}")


def valid_korean_coordinates(latitude: Any, longitude: Any) -> bool:
    return (
        isinstance(latitude, (int, float))
        and isinstance(longitude, (int, float))
        and 33 <= float(latitude) <= 39
        and 124 <= float(longitude) <= 132
    )


def geocode_address(*, api_key: str, address: str, timeout: int = 15) -> tuple[float, float]:
    query = urllib.parse.urlencode({"query": address})
    request = urllib.request.Request(
        f"{KAKAO_ADDRESS_URL}?{query}",
        headers={
            "Authorization": f"KakaoAK {api_key}",
            "User-Agent": "JobScout/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    documents = payload.get("documents") or []
    if not documents:
        raise RuntimeError("worksite address could not be geocoded")
    return float(documents[0]["y"]), float(documents[0]["x"])


def job_coordinates(job: dict[str, Any], *, api_key: str) -> tuple[float, float]:
    latitude = job.get("latitude")
    longitude = job.get("longitude")
    if valid_korean_coordinates(latitude, longitude):
        return float(latitude), float(longitude)
    detail = fetch_job_detail(int(job["recruit_no"]))
    latitude = detail.get("latitude")
    longitude = detail.get("longitude")
    if valid_korean_coordinates(latitude, longitude):
        return float(latitude), float(longitude)
    address = str(
        detail.get("workingAddress")
        or job.get("working_address")
        or job.get("area")
        or ""
    ).strip()
    if not address:
        raise RuntimeError("posting has no verified worksite coordinates or address")
    return geocode_address(api_key=api_key, address=address)


def assess_job(job: dict[str, Any], *, api_key: str) -> dict[str, Any]:
    base = {
        "recruit_no": int(job["recruit_no"]),
        "title": str(job.get("title") or ""),
        "company": str(job.get("company") or ""),
        "area": str(job.get("area") or ""),
        "working_address": str(job.get("working_address") or ""),
        "url": str(job.get("url") or ""),
    }
    # Coupang campaign ads often show a shuttle/pickup point rather than the
    # actual center. A commute cannot pass the user's hard cutoff until the
    # center itself is known.
    if job.get("logistics_category") == "Coupang Logistics Services":
        return {
            **base,
            "route_status": "actual_worksite_unverified",
            "distance_excluded": True,
            "distance_exclusion_reason": (
                "실제 쿠팡 물류센터 주소가 확인되지 않아 50분 미만 통근을 검증할 수 없음"
            ),
        }
    try:
        latitude, longitude = job_coordinates(job, api_key=api_key)
        payload = read_kakao_route(
            api_key=api_key,
            latitude=latitude,
            longitude=longitude,
        )
        return {
            **base,
            "latitude": latitude,
            "longitude": longitude,
            **summarize_route(payload),
        }
    except Exception as exc:
        return {
            **base,
            "route_status": "unverified",
            "distance_excluded": True,
            "distance_exclusion_reason": f"대중교통 통근시간 검증 실패: {exc}",
        }


def route_cache_key(job: dict[str, Any]) -> str:
    """Key a route by worksite so reposted job IDs can share one lookup."""

    address = "".join(
        str(job.get("working_address") or "").lower().split()
    )
    if address:
        digest = hashlib.sha256(address.encode("utf-8")).hexdigest()
        return f"address:{digest}"
    latitude = job.get("latitude")
    longitude = job.get("longitude")
    if valid_korean_coordinates(latitude, longitude):
        return f"coordinates:{float(latitude):.5f},{float(longitude):.5f}"
    return f"recruit:{int(job['recruit_no'])}"


def _cache_entry_is_fresh(
    entry: dict[str, Any], *, now: datetime, max_age_hours: int
) -> bool:
    try:
        checked_at = datetime.fromisoformat(str(entry.get("checked_at") or ""))
    except ValueError:
        return False
    if checked_at.tzinfo is None:
        checked_at = checked_at.astimezone()
    return now - checked_at <= timedelta(hours=max_age_hours)


def _route_only(result: dict[str, Any]) -> dict[str, Any]:
    identity_fields = {
        "recruit_no",
        "title",
        "company",
        "area",
        "working_address",
        "url",
    }
    return {
        key: value for key, value in result.items() if key not in identity_fields
    }


def _job_identity(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "recruit_no": int(job["recruit_no"]),
        "title": str(job.get("title") or ""),
        "company": str(job.get("company") or ""),
        "area": str(job.get("area") or ""),
        "working_address": str(job.get("working_address") or ""),
        "url": str(job.get("url") or ""),
    }


def build_cache(
    report: dict[str, Any],
    *,
    api_key: str,
    workers: int,
    existing_cache: dict[str, Any] | None = None,
    max_age_hours: int = DEFAULT_ROUTE_CACHE_HOURS,
) -> dict[str, Any]:
    jobs = [*(report.get("jobs") or []), *(report.get("logistics_jobs") or [])]
    now = datetime.now().astimezone()
    existing_routes = (existing_cache or {}).get("routes") or {}
    if not isinstance(existing_routes, dict):
        existing_routes = {}
    # Migrate the previous per-recruit cache shape into address-keyed entries.
    if not existing_routes:
        for item in ((existing_cache or {}).get("jobs") or {}).values():
            if not isinstance(item, dict) or "recruit_no" not in item:
                continue
            existing_routes[route_cache_key(item)] = {
                "checked_at": (existing_cache or {}).get("checked_at") or "",
                "result": _route_only(item),
            }

    groups: dict[str, list[dict[str, Any]]] = {}
    for job in jobs:
        groups.setdefault(route_cache_key(job), []).append(job)

    route_results: dict[str, dict[str, Any]] = {}
    routes_to_fetch: dict[str, dict[str, Any]] = {}
    for key, group in groups.items():
        cached = existing_routes.get(key)
        if (
            isinstance(cached, dict)
            and isinstance(cached.get("result"), dict)
            and _cache_entry_is_fresh(
                cached, now=now, max_age_hours=max_age_hours
            )
        ):
            route_results[key] = cached["result"]
        else:
            routes_to_fetch[key] = group[0]

    if routes_to_fetch:
        with ThreadPoolExecutor(
            max_workers=min(workers, len(routes_to_fetch))
        ) as executor:
            future_to_key = {
                executor.submit(assess_job, job, api_key=api_key): key
                for key, job in routes_to_fetch.items()
            }
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    route_results[key] = _route_only(future.result())
                except Exception as exc:
                    route_results[key] = {
                        "route_status": "unverified",
                        "distance_excluded": True,
                        "distance_exclusion_reason": (
                            f"대중교통 통근시간 검증 실패: {exc}"
                        ),
                    }

    results: dict[str, dict[str, Any]] = {}
    for key, group in groups.items():
        for job in group:
            result = {
                **_job_identity(job),
                **route_results[key],
                "route_cache_key": key,
            }
            results[str(result["recruit_no"])] = result
    routes = {
        key: {
            "checked_at": (
                existing_routes[key]["checked_at"]
                if key in existing_routes
                and key not in routes_to_fetch
                and isinstance(existing_routes[key], dict)
                else now.isoformat(timespec="seconds")
            ),
            "result": route_results[key],
        }
        for key in groups
    }
    return {
        "checked_at": now.isoformat(timespec="seconds"),
        "provider": "Kakao Maps public-transit routing",
        "origin": ORIGIN,
        "allowed_modes": ["bus", "subway/train"],
        "max_transit_minutes": MAX_TRANSIT_MINUTES,
        "cutoff_operator": "<",
        "job_count": len(results),
        "unique_route_count": len(groups),
        "route_cache_hit_count": sum(
            len(groups[key]) for key in groups if key not in routes_to_fetch
        ),
        "route_request_count": len(routes_to_fetch),
        "route_cache_max_age_hours": max_age_hours,
        "excluded_count": sum(
            bool(item.get("distance_excluded")) for item in results.values()
        ),
        "routes": dict(sorted(routes.items())),
        "jobs": dict(sorted(results.items(), key=lambda item: int(item[0]))),
    }


def reclassify_cache(
    cache: dict[str, Any], *, max_minutes: int = MAX_TRANSIT_MINUTES
) -> dict[str, Any]:
    """Apply a changed cutoff to cached routes without repeating API calls."""

    def reclassify_item(item: dict[str, Any]) -> None:
        if item.get("route_status") != "verified":
            item["distance_excluded"] = True
            if "60분 이내" in str(item.get("distance_exclusion_reason") or ""):
                item["distance_exclusion_reason"] = str(
                    item["distance_exclusion_reason"]
                ).replace("60분 이내", f"{max_minutes}분 미만")
            return
        seconds = item.get("transit_seconds")
        if not isinstance(seconds, int):
            item["distance_excluded"] = True
            item["distance_exclusion_reason"] = (
                "캐시에 정확한 대중교통 시간이 없어 재검증 필요"
            )
            return
        minutes = math.ceil(seconds / 60)
        excluded = minutes >= max_minutes
        item["transit_minutes"] = minutes
        item["distance_excluded"] = excluded
        item["distance_exclusion_reason"] = (
            f"잠실새내역에서 대중교통 최단 {minutes}분으로 "
            f"{max_minutes}분 미만 기준 미충족"
            if excluded
            else ""
        )

    jobs = cache.get("jobs") or {}
    if not isinstance(jobs, dict):
        raise ValueError("transit cache must contain a jobs object")
    for item in jobs.values():
        if isinstance(item, dict):
            reclassify_item(item)
    routes = cache.get("routes") or {}
    if isinstance(routes, dict):
        for entry in routes.values():
            result = entry.get("result") if isinstance(entry, dict) else None
            if isinstance(result, dict):
                reclassify_item(result)
    cache["max_transit_minutes"] = max_minutes
    cache["cutoff_operator"] = "<"
    cache["cutoff_updated_at"] = datetime.now().astimezone().isoformat(
        timespec="seconds"
    )
    cache["excluded_count"] = sum(
        bool(item.get("distance_excluded"))
        for item in jobs.values()
        if isinstance(item, dict)
    )
    return cache


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument(
        "--route-cache-hours",
        type=int,
        default=DEFAULT_ROUTE_CACHE_HOURS,
        help="Reuse route results for this many hours, keyed by actual worksite.",
    )
    parser.add_argument(
        "--reclassify-cache",
        action="store_true",
        help="Reuse route durations already stored at --output and apply the current cutoff",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.reclassify_cache:
        if not args.output.exists():
            raise SystemExit(f"transit cache not found: {args.output}")
        cache = reclassify_cache(
            json.loads(args.output.read_text(encoding="utf-8"))
        )
        args.output.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"Reclassified {cache.get('job_count', len(cache.get('jobs') or {}))} "
            f"cached commutes; excluded {cache['excluded_count']} under the "
            f"strict <{cache['max_transit_minutes']}-minute rule."
        )
        print(args.output)
        return 0
    api_key = os.environ.get("KAKAO_REST_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("KAKAO_REST_API_KEY is required for public-transit routing")
    report = json.loads(args.input.read_text(encoding="utf-8"))
    existing_cache = (
        json.loads(args.output.read_text(encoding="utf-8"))
        if args.output.exists()
        else None
    )
    cache = build_cache(
        report,
        api_key=api_key,
        workers=max(1, args.workers),
        existing_cache=existing_cache,
        max_age_hours=max(1, args.route_cache_hours),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Checked {cache['job_count']} commutes; "
        f"requested {cache['route_request_count']} unique routes and reused "
        f"{cache['route_cache_hit_count']} cached job routes; "
        f"excluded {cache['excluded_count']} under the strict "
        f"<{cache['max_transit_minutes']}-minute rule."
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
