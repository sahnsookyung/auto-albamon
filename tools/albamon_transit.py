#!/usr/bin/env python3
"""Store and reclassify Kakao routes from Jamsil-dong 189-4.

Ordinary commute checks prefer Kakao's documented REST route endpoint. A
persistent, fail-closed daily budget keeps scheduled calls below the published
free allowance; entries that cannot use the API are explicitly queued for a
Kakao Maps web-UI pass. API keys are used only in request headers and are never
written to caches or reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback for local development.
    fcntl = None

try:
    from tools.albamon_jobs import fetch_job_detail
    from tools.albamon_runtime import atomic_write_text
except ModuleNotFoundError:
    from albamon_jobs import fetch_job_detail
    from albamon_runtime import atomic_write_text


KAKAO_TRANSIT_URL = "https://dapi.kakao.com/v2/routing/publictraffic"
KAKAO_ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"
DEFAULT_INPUT = Path("artifacts/albamon/latest-combined.json")
DEFAULT_OUTPUT = Path("tools/albamon_transit_cache.json")
DEFAULT_LATE_NIGHT_OUTPUT = Path("tools/albamon_late_night_route_cache.json")
DEFAULT_WORKERS = 6
ORIGIN = {
    "name": "잠실동 189-4",
    "address": "서울특별시 송파구 잠실동 189-4",
    "latitude": 37.5099418,
    "longitude": 127.0823805,
}
MAX_TRANSIT_MINUTES = 50
DEFAULT_ROUTE_CACHE_HOURS = 24
DEFAULT_ROUTE_REFRESH_HOUR = 10
KAKAO_PUBLIC_TRANSIT_FREE_DAILY_QUOTA = 1_000
DEFAULT_KAKAO_PUBLIC_TRANSIT_DAILY_LIMIT = 900
DEFAULT_KAKAO_REQUESTS_PER_SECOND = 4.0
KAKAO_RATE_LIMIT_COOLDOWN_SECONDS = 5.0
DEFAULT_KAKAO_QUOTA_STATE = Path(
    "artifacts/albamon/cache/kakao_public_transit_quota.json"
)
KOREA_TIMEZONE = ZoneInfo("Asia/Seoul")
LATE_NIGHT_SHIFT_RE = re.compile(
    r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)\s*[~\-]\s*"
    r"([01]?\d|2[0-3]):([0-5]\d)(?!\d)"
)
VERIFIED_WORKSITE_ADDRESS_ALIASES = {
    # Naver Map WebUI verified 2026-08-16. Albamon listings alternate between
    # the venue name and road address for the same worksite.
    "신세계백화점강남점": "서울 서초구 신반포로 176",
    "서울서초구신반포로176": "서울 서초구 신반포로 176",
}


class KakaoQuotaExhausted(RuntimeError):
    """Raised before an API call that could exceed the configured free budget."""


class KakaoApiUnavailable(KakaoQuotaExhausted):
    """Raised when Kakao rejects this app/key before route processing."""


class KakaoRequestPacer:
    """Serialize Kakao request starts across route worker threads."""

    def __init__(
        self,
        requests_per_second: float = DEFAULT_KAKAO_REQUESTS_PER_SECOND,
        *,
        monotonic: Any | None = None,
        sleeper: Any | None = None,
    ) -> None:
        if requests_per_second <= 0:
            raise ValueError("Kakao requests per second must be positive")
        self.requests_per_second = float(requests_per_second)
        self.interval_seconds = 1.0 / self.requests_per_second
        self._monotonic = monotonic or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._lock = threading.Lock()
        self._next_request_at = 0.0

    def wait(self) -> float:
        """Wait for and reserve the next request start time."""

        total_delay = 0.0
        while True:
            with self._lock:
                now = self._monotonic()
                delay = max(0.0, self._next_request_at - now)
                if not delay:
                    self._next_request_at = now + self.interval_seconds
                    return total_delay
            # Do not hold the lock while sleeping: a rate-limit response from
            # another worker must be able to extend the shared cooldown.
            self._sleeper(delay)
            total_delay += delay

    def defer(self, seconds: float) -> None:
        """Apply one shared cooldown after Kakao reports a burst limit."""

        if seconds < 0:
            raise ValueError("Kakao cooldown must not be negative")
        with self._lock:
            self._next_request_at = max(
                self._next_request_at,
                self._monotonic() + seconds,
            )


class KakaoQuotaBudget:
    """Persist a conservative, Korea-date Kakao public-transit call budget."""

    def __init__(
        self,
        path: Path,
        *,
        daily_limit: int = DEFAULT_KAKAO_PUBLIC_TRANSIT_DAILY_LIMIT,
        now_provider: Any | None = None,
    ) -> None:
        if not 1 <= daily_limit <= KAKAO_PUBLIC_TRANSIT_FREE_DAILY_QUOTA:
            raise ValueError(
                "Kakao public-transit daily limit must be between 1 and "
                f"{KAKAO_PUBLIC_TRANSIT_FREE_DAILY_QUOTA}"
            )
        self.path = path
        self.daily_limit = daily_limit
        self._now_provider = now_provider or (lambda: datetime.now(KOREA_TIMEZONE))
        self._thread_lock = threading.Lock()

    def _now(self) -> datetime:
        value = self._now_provider()
        if value.tzinfo is None:
            value = value.replace(tzinfo=KOREA_TIMEZONE)
        return value.astimezone(KOREA_TIMEZONE)

    def _empty_state(self, now: datetime) -> dict[str, Any]:
        return {
            "version": 1,
            "date_kst": now.date().isoformat(),
            "daily_limit": self.daily_limit,
            "used": 0,
            "remote_exhausted": False,
            "updated_at": now.isoformat(timespec="seconds"),
        }

    def _load_state(self, now: datetime) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_state(now)
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KakaoQuotaExhausted(
                "local Kakao quota ledger is unreadable; WebUI fallback required"
            ) from exc
        if not isinstance(state, dict) or state.get("version") != 1:
            raise KakaoQuotaExhausted(
                "local Kakao quota ledger is invalid; WebUI fallback required"
            )
        if state.get("date_kst") != now.date().isoformat():
            return self._empty_state(now)
        used = state.get("used")
        if not isinstance(used, int) or used < 0:
            raise KakaoQuotaExhausted(
                "local Kakao quota ledger count is invalid; WebUI fallback required"
            )
        state["daily_limit"] = self.daily_limit
        return state

    def _write_state(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.path,
            json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
        )

    def _locked_update(self, callback: Any) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with self._thread_lock, lock_path.open("a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                now = self._now()
                state = self._load_state(now)
                callback(state, now)
                self._write_state(state)
                return dict(state)
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def reserve(self) -> dict[str, Any]:
        """Reserve one call before sending it, or fail closed to the WebUI."""

        def update(state: dict[str, Any], now: datetime) -> None:
            if state.get("remote_exhausted"):
                reason = str(state.get("blocked_reason") or "quota_429")
                error_type = (
                    KakaoApiUnavailable if reason.startswith("http_") else KakaoQuotaExhausted
                )
                raise error_type(
                    f"Kakao route API is blocked for today ({reason}); "
                    "WebUI fallback required"
                )
            if int(state["used"]) >= self.daily_limit:
                raise KakaoQuotaExhausted(
                    "Kakao public-transit free-call budget is exhausted; "
                    "WebUI fallback required"
                )
            state["used"] = int(state["used"]) + 1
            state["updated_at"] = now.isoformat(timespec="seconds")

        return self._locked_update(update)

    def mark_remote_exhausted(self, reason: str = "quota_429") -> dict[str, Any]:
        """Stop further calls after a quota or app-access rejection."""

        def update(state: dict[str, Any], now: datetime) -> None:
            state["remote_exhausted"] = True
            state["blocked_reason"] = reason
            state["updated_at"] = now.isoformat(timespec="seconds")

        return self._locked_update(update)

    def clear_access_block(self) -> dict[str, Any]:
        """Allow one retry after the user fixes an HTTP 401/403 app setting."""

        def update(state: dict[str, Any], now: datetime) -> None:
            reason = str(state.get("blocked_reason") or "")
            if not reason.startswith(("http_401", "http_403")):
                return
            state["remote_exhausted"] = False
            state.pop("blocked_reason", None)
            state["updated_at"] = now.isoformat(timespec="seconds")

        return self._locked_update(update)

    def snapshot(self) -> dict[str, Any]:
        """Return a privacy-safe view of the current local allowance."""

        try:
            with self._thread_lock:
                now = self._now()
                state = self._load_state(now)
        except KakaoQuotaExhausted:
            return {
                "date_kst": self._now().date().isoformat(),
                "daily_limit": self.daily_limit,
                "used": self.daily_limit,
                "remaining": 0,
                "remote_exhausted": True,
                "ledger_error": True,
            }
        used = min(int(state["used"]), self.daily_limit)
        return {
            "date_kst": state["date_kst"],
            "daily_limit": self.daily_limit,
            "used": used,
            "remaining": 0
            if state.get("remote_exhausted")
            else max(0, self.daily_limit - used),
            "remote_exhausted": bool(state.get("remote_exhausted")),
            **(
                {"blocked_reason": str(state["blocked_reason"])}
                if state.get("blocked_reason")
                else {}
            ),
        }


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
    """Normalize a Kakao route response and apply the inclusive base cutoff."""

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
    # Kakao displays rounded-up whole minutes, so compare that displayed value
    # directly with the inclusive upper bound.
    excluded = minutes > max_minutes
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
            f"{ORIGIN['name']}에서 대중교통 최단 {minutes}분으로 "
            f"{max_minutes}분 이하 기준 미충족"
            if excluded
            else ""
        ),
    }


def kakao_error_code(error: urllib.error.HTTPError) -> int | None:
    """Read Kakao's documented numeric error code without retaining its body."""

    try:
        payload = json.loads(error.read(4_096).decode("utf-8"))
    except (AttributeError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    code = payload.get("code") if isinstance(payload, dict) else None
    return code if isinstance(code, int) else None


def read_kakao_route(
    *,
    api_key: str,
    latitude: float,
    longitude: float,
    timeout: int = 15,
    quota_budget: KakaoQuotaBudget | None = None,
    request_pacer: KakaoRequestPacer | None = None,
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
            # Every HTTP attempt consumes quota, including a retry after a
            # transient server/network response, so reserve each one.
            if request_pacer is not None:
                request_pacer.wait()
            if quota_budget is not None:
                quota_budget.reserve()
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_code = kakao_error_code(exc)
            if error_code == -10:
                # Kakao documents -10 for both daily quota exhaustion and a
                # per-second limit. Retry this idempotent GET once after a
                # shared cooldown before treating it as a daily block.
                if attempt == 0:
                    if request_pacer is not None:
                        request_pacer.defer(KAKAO_RATE_LIMIT_COOLDOWN_SECONDS)
                    else:
                        time.sleep(KAKAO_RATE_LIMIT_COOLDOWN_SECONDS)
                    continue
                if quota_budget is not None:
                    quota_budget.mark_remote_exhausted("quota_-10")
                raise KakaoQuotaExhausted(
                    "Kakao kept reporting API limit -10 after a paced cooldown; "
                    "WebUI fallback required"
                ) from exc
            if exc.code == 429 or error_code == -11:
                if quota_budget is not None:
                    quota_budget.mark_remote_exhausted(
                        f"quota_{error_code}" if error_code is not None else "quota_429"
                    )
                raise KakaoQuotaExhausted(
                    "Kakao reported its API quota or billing ceiling exhausted; "
                    "WebUI fallback required"
                ) from exc
            if exc.code in {401, 403}:
                if quota_budget is not None:
                    quota_budget.mark_remote_exhausted(
                        f"http_{exc.code}_code_{error_code}"
                        if error_code is not None
                        else f"http_{exc.code}"
                    )
                raise KakaoApiUnavailable(
                    f"Kakao rejected route API access (HTTP {exc.code}"
                    f"{f', code {error_code}' if error_code is not None else ''}); "
                    "WebUI fallback required"
                ) from exc
            last_error = exc
            if exc.code < 500 or attempt > 0:
                break
            time.sleep(0.4)
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


def canonical_worksite_address(address: str) -> str:
    compact = re.sub(r"[^0-9a-z가-힣]", "", str(address or "").lower())
    for alias, canonical in VERIFIED_WORKSITE_ADDRESS_ALIASES.items():
        if alias in compact:
            return canonical
    return " ".join(str(address or "").split())


def geocode_query_candidates(address: str) -> list[str]:
    """Return bounded exact-to-coarse variants for floor-decorated addresses."""

    clean = canonical_worksite_address(address)
    candidates = [clean] if clean else []
    without_parenthetical = re.sub(r"\s*\([^)]*\).*$", "", clean).strip()
    without_floor = (
        re.sub(
            r"\s+(?:지하\s*)?\d+(?:\s*,\s*\d+)*층.*$",
            "",
            clean,
        ).strip()
        if "(" not in clean
        else without_parenthetical
    )
    for candidate in (without_parenthetical, without_floor):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates[:3]


def geocode_address(
    *,
    api_key: str,
    address: str,
    timeout: int = 15,
    request_pacer: KakaoRequestPacer | None = None,
) -> tuple[float, float]:
    for candidate in geocode_query_candidates(address):
        query = urllib.parse.urlencode({"query": candidate})
        request = urllib.request.Request(
            f"{KAKAO_ADDRESS_URL}?{query}",
            headers={
                "Authorization": f"KakaoAK {api_key}",
                "User-Agent": "JobScout/1.0",
            },
        )
        if request_pacer is not None:
            request_pacer.wait()
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        documents = payload.get("documents") or []
        if documents:
            return float(documents[0]["y"]), float(documents[0]["x"])
    raise RuntimeError("worksite address could not be geocoded")


def job_coordinates(
    job: dict[str, Any],
    *,
    api_key: str,
    request_pacer: KakaoRequestPacer | None = None,
) -> tuple[float, float]:
    latitude = job.get("latitude")
    longitude = job.get("longitude")
    if valid_korean_coordinates(latitude, longitude):
        return float(latitude), float(longitude)
    detail = fetch_job_detail(int(job["recruit_no"]))
    latitude = detail.get("latitude")
    longitude = detail.get("longitude")
    if valid_korean_coordinates(latitude, longitude):
        return float(latitude), float(longitude)
    address = canonical_worksite_address(str(
        detail.get("workingAddress")
        or job.get("working_address")
        or job.get("area")
        or ""
    ))
    if not address:
        raise RuntimeError("posting has no verified worksite coordinates or address")
    return geocode_address(
        api_key=api_key,
        address=address,
        request_pacer=request_pacer,
    )


def assess_job(
    job: dict[str, Any],
    *,
    api_key: str,
    quota_budget: KakaoQuotaBudget | None = None,
    request_pacer: KakaoRequestPacer | None = None,
) -> dict[str, Any]:
    base = {
        "recruit_no": int(job["recruit_no"]),
        "title": str(job.get("title") or ""),
        "company": str(job.get("company") or ""),
        "area": str(job.get("area") or ""),
        "working_address": str(job.get("working_address") or ""),
        "url": str(job.get("url") or ""),
    }
    if job.get("remote_job") or "remote_short_term" in (
        job.get("eligibility_lanes") or []
    ):
        return {
            **base,
            "route_status": "not_required",
            "transit_seconds": 0,
            "transit_minutes": 0,
            "transit_type": "REMOTE",
            "transfers": 0,
            "vehicles": [],
            "distance_excluded": False,
            "distance_exclusion_reason": "",
            "route_provider": "none",
            "route_reuse_basis": "remote work; no commute required",
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
                "실제 쿠팡 물류센터 주소가 확인되지 않아 통근 기준을 검증할 수 없음"
            ),
        }
    try:
        latitude, longitude = job_coordinates(
            job,
            api_key=api_key,
            request_pacer=request_pacer,
        )
        payload = read_kakao_route(
            api_key=api_key,
            latitude=latitude,
            longitude=longitude,
            quota_budget=quota_budget,
            request_pacer=request_pacer,
        )
        return {
            **base,
            "latitude": latitude,
            "longitude": longitude,
            **summarize_route(payload),
            "route_provider": "Kakao Map REST API",
            "route_reuse_basis": "official Kakao public-transit REST route",
            "route_source_checked_at": datetime.now(KOREA_TIMEZONE).isoformat(
                timespec="seconds"
            ),
            "route_time_assumption": (
                "current Kakao public-transit pattern; the endpoint has no "
                "future departure-time parameter"
            ),
        }
    except KakaoApiUnavailable as exc:
        return {
            **base,
            "route_status": "unverified",
            "distance_excluded": True,
            "distance_exclusion_reason": str(exc),
            "route_webui_fallback_required": True,
            "route_fallback_reason": "kakao_api_unavailable",
        }
    except KakaoQuotaExhausted as exc:
        return {
            **base,
            "route_status": "unverified",
            "distance_excluded": True,
            "distance_exclusion_reason": str(exc),
            "route_webui_fallback_required": True,
            "route_fallback_reason": "kakao_quota_exhausted",
        }
    except Exception as exc:
        return {
            **base,
            "route_status": "unverified",
            "distance_excluded": True,
            "distance_exclusion_reason": f"대중교통 통근시간 검증 실패: {exc}",
            "route_webui_fallback_required": True,
            "route_fallback_reason": "kakao_api_error",
        }


def route_cache_key(job: dict[str, Any]) -> str:
    """Key a route by worksite so reposted job IDs can share one lookup."""

    if job.get("remote_job") or "remote_short_term" in (
        job.get("eligibility_lanes") or []
    ):
        return "remote:no-commute"
    address = "".join(
        canonical_worksite_address(job.get("working_address") or "")
        .lower()
        .split()
    )
    if address:
        digest = hashlib.sha256(address.encode("utf-8")).hexdigest()
        return f"address:{digest}"
    latitude = job.get("latitude")
    longitude = job.get("longitude")
    if valid_korean_coordinates(latitude, longitude):
        return f"coordinates:{float(latitude):.5f},{float(longitude):.5f}"
    return f"recruit:{int(job['recruit_no'])}"


def _compact_location(value: Any) -> str:
    """Normalize a Korean address fragment for conservative cache matching."""

    return re.sub(r"[^0-9a-z가-힣]", "", str(value or "").lower())


def load_late_night_route_cache(path: Path) -> dict[str, Any] | None:
    """Load browser-captured transit and bicycle routes for late-night jobs."""

    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("routes"), list):
        raise ValueError(f"late-night route cache has an incompatible shape: {path}")
    cached_origin = payload.get("origin") or {}
    if (
        _compact_location(cached_origin.get("name")) != _compact_location(ORIGIN["name"])
        or _compact_location(cached_origin.get("address"))
        != _compact_location(ORIGIN["address"])
    ):
        raise ValueError("late-night route cache uses a different origin")
    for route in payload["routes"]:
        if not isinstance(route, dict):
            raise ValueError("late-night route entries must be objects")
        exact_routes = route.get("exact_shift_routes")
        if exact_routes is None:
            continue
        if not isinstance(exact_routes, dict):
            raise ValueError("exact_shift_routes must be an object keyed by shift")
        for shift, exact in exact_routes.items():
            if not LATE_NIGHT_SHIFT_RE.fullmatch(str(shift)) or not isinstance(
                exact, dict
            ):
                raise ValueError(f"invalid exact future route entry: {shift}")
            for leg_name in ("outbound", "return"):
                leg = exact.get(leg_name)
                if leg is None:
                    continue
                if not isinstance(leg, dict):
                    raise ValueError(f"{shift} {leg_name} route must be an object")
                elapsed = leg.get("elapsed_minutes")
                if leg.get("available") is not False and (
                    not isinstance(elapsed, int) or elapsed <= 0
                ):
                    raise ValueError(
                        f"{shift} {leg_name} requires positive elapsed_minutes"
                    )
    return payload


def late_night_route_for_job(
    job: dict[str, Any], cache: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Find the web-UI route whose explicit address fragment matches a job."""

    if not cache:
        return None
    address = _compact_location(
        job.get("working_address") or job.get("area") or ""
    )
    matches = [
        route
        for route in cache.get("routes") or []
        if isinstance(route, dict)
        and _compact_location(route.get("address_match"))
        and _compact_location(route.get("address_match")) in address
    ]
    if not matches:
        return None
    return max(matches, key=lambda route: len(_compact_location(route["address_match"])))


def late_night_shift_ranges(job: dict[str, Any]) -> list[tuple[str, str]]:
    """Return distinct advertised start/end times for the evening lane."""

    ranges: list[tuple[str, str]] = []
    for option in job.get("late_night_shift_options") or []:
        # The collector prefixes each evidence string with the one shift that
        # passed the 19:00-02:00 lane. The evidence after the dash can repeat
        # unrelated daytime options, which must not become commute journeys.
        match = LATE_NIGHT_SHIFT_RE.search(str(option))
        if match is None:
            continue
        start = f"{int(match.group(1)):02d}:{match.group(2)}"
        end = f"{int(match.group(3)):02d}:{match.group(4)}"
        pair = (start, end)
        if pair not in ranges:
            ranges.append(pair)
    return ranges


def build_late_night_journeys(
    job: dict[str, Any], route: dict[str, Any]
) -> list[dict[str, Any]]:
    """Attach advertised times to Naver future routes or conservative fallbacks."""

    outbound_minutes = route.get("outbound_transit_pattern_minutes")
    return_minutes = route.get("return_transit_pattern_minutes")
    night_pattern = route.get("night_transit_pattern")
    bike = {
        "mode": "bicycle_road_priority",
        "minutes": route.get("bicycle_road_priority_minutes"),
        "distance_km": route.get("bicycle_road_priority_distance_km"),
        "shortest_minutes": route.get("bicycle_shortest_minutes"),
        "shortest_distance_km": route.get("bicycle_shortest_distance_km"),
    }
    journeys: list[dict[str, Any]] = []
    for start, end in late_night_shift_ranges(job):
        shift = f"{start}~{end}"
        exact_routes = route.get("exact_shift_routes") or {}
        exact = exact_routes.get(shift) if isinstance(exact_routes, dict) else None
        if not isinstance(exact, dict):
            exact = None

        def exact_leg(name: str, *, at: str) -> dict[str, Any] | None:
            leg = exact.get(name) if exact else None
            if (
                not isinstance(leg, dict)
                or leg.get("available") is False
                or not isinstance(leg.get("elapsed_minutes"), int)
            ):
                return None
            granularity = leg.get("future_time_granularity_minutes")
            if not isinstance(granularity, int) or granularity <= 0:
                granularity = 10
            return {
                "at": at,
                "mode": "public_transit_future_time",
                "minutes": leg["elapsed_minutes"],
                "displayed_route_minutes": leg.get("displayed_route_minutes"),
                "vehicles": leg.get("vehicles") or [],
                "provider": str(leg.get("provider") or "Naver Map web UI"),
                "departure_at": str(leg.get("departure_at") or ""),
                "arrival_at": str(leg.get("arrival_at") or ""),
                "service_date": str(
                    leg.get("service_date")
                    or (exact or {}).get("service_date")
                    or ""
                ),
                "future_time_granularity_minutes": granularity,
                # The route is exact for the representative date stored in the
                # cache; a later real work date can have a different timetable.
                "requires_day_of_service_check": True,
                "future_time_verified": True,
            }

        exact_outbound = exact_leg("outbound", at=start)
        exact_return = exact_leg("return", at=end)
        end_hour, end_minute = (int(value) for value in end.split(":"))
        after_midnight = end_hour * 60 + end_minute <= 2 * 60
        if exact_return is not None:
            return_route = exact_return
        elif exact and isinstance(exact.get("return"), dict):
            return_route = {
                "at": end,
                **bike,
                "future_transit_unavailable": True,
                "future_transit_checked_at": str(
                    exact["return"].get("departure_at") or ""
                ),
                "requires_day_of_service_check": False,
            }
        elif after_midnight and isinstance(night_pattern, dict):
            return_route = {
                "at": end,
                "mode": "night_bus_pattern",
                "minutes": night_pattern.get("minutes"),
                "vehicles": night_pattern.get("vehicles") or [],
                "requires_day_of_service_check": True,
            }
        elif after_midnight:
            return_route = {
                "at": end,
                **bike,
                "requires_day_of_service_check": False,
            }
        else:
            return_route = {
                "at": end,
                "mode": "public_transit_pattern",
                "minutes": return_minutes,
                "vehicles": [],
                "requires_day_of_service_check": True,
            }
        journeys.append(
            {
                "shift": shift,
                "outbound": exact_outbound
                or {
                    "at": start,
                    "mode": "public_transit_pattern",
                    "minutes": outbound_minutes,
                    "requires_day_of_service_check": True,
                },
                "return": return_route,
                "bicycle_fallback": dict(bike),
            }
        )
    return journeys


def merge_late_night_routes(
    report: dict[str, Any],
    existing_cache: dict[str, Any] | None,
    late_night_cache: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Seed the normal cache with browser-captured, time-aware evening routes."""

    if not late_night_cache:
        return existing_cache
    if existing_cache and not cache_origin_matches(existing_cache):
        raise ValueError("transit cache origin differs from the configured origin")
    cache = dict(existing_cache or {"origin": dict(ORIGIN), "routes": {}, "jobs": {}})
    routes = dict(cache.get("routes") or {})
    jobs = [*(report.get("jobs") or []), *(report.get("logistics_jobs") or [])]
    for job in jobs:
        if "songpa_late_night" not in (job.get("eligibility_lanes") or []):
            continue
        route = late_night_route_for_job(job, late_night_cache)
        if route is None:
            continue
        minutes = route.get("outbound_transit_pattern_minutes")
        if not isinstance(minutes, int):
            continue
        excluded = minutes > MAX_TRANSIT_MINUTES
        result = {
            "route_status": "verified",
            "transit_seconds": minutes * 60,
            "transit_minutes": minutes,
            "transit_type": "BUS_OR_SUBWAY_PATTERN",
            "transfers": None,
            "vehicles": [],
            "landing_url": "https://map.kakao.com/",
            "distance_excluded": excluded,
            "distance_exclusion_reason": (
                f"{ORIGIN['name']}에서 근무 시작 시간대 대중교통 경로 {minutes}분으로 "
                f"{MAX_TRANSIT_MINUTES}분 이하 기준 미충족"
                if excluded
                else ""
            ),
            "route_provider": str(late_night_cache.get("provider") or "Kakao Maps web UI"),
            "route_reuse_basis": "fresh exact-worksite Kakao Maps web-UI late-night pass",
            "route_source_checked_at": route.get("checked_at") or late_night_cache.get("checked_at"),
            "route_time_assumption": str(late_night_cache.get("route_time_policy") or ""),
            "late_night_route_checked_at": route.get("checked_at") or late_night_cache.get("checked_at"),
            "late_night_route_time_policy": str(late_night_cache.get("route_time_policy") or ""),
            # Route entries are shared by every listing at the same worksite. Keep
            # only route-level measurements here; build each job's exact shift
            # journeys later when expanding the route back into per-job results.
            "late_night_route_profile": {
                "outbound_transit_pattern_minutes": route.get(
                    "outbound_transit_pattern_minutes"
                ),
                "return_transit_pattern_minutes": route.get(
                    "return_transit_pattern_minutes"
                ),
                "night_transit_pattern": route.get("night_transit_pattern"),
                "exact_shift_routes": route.get("exact_shift_routes") or {},
                "bicycle_road_priority_minutes": route.get(
                    "bicycle_road_priority_minutes"
                ),
                "bicycle_road_priority_distance_km": route.get(
                    "bicycle_road_priority_distance_km"
                ),
                "bicycle_shortest_minutes": route.get("bicycle_shortest_minutes"),
                "bicycle_shortest_distance_km": route.get(
                    "bicycle_shortest_distance_km"
                ),
            },
            "return_bicycle_minutes": route.get("bicycle_road_priority_minutes"),
            "return_bicycle_distance_km": route.get("bicycle_road_priority_distance_km"),
        }
        routes[route_cache_key(job)] = {
            "checked_at": route.get("checked_at") or late_night_cache.get("checked_at"),
            "result": result,
        }
    modes = list(cache.get("allowed_modes") or ["bus", "subway/train"])
    if "bicycle return fallback" not in modes:
        modes.append("bicycle return fallback")
    cache.update(
        {
            "origin": dict(ORIGIN),
            "routes": routes,
            "allowed_modes": modes,
            "late_night_route_cache_checked_at": late_night_cache.get("checked_at"),
        }
    )
    return cache


def _cache_entry_is_fresh(
    entry: dict[str, Any],
    *,
    now: datetime,
    max_age_hours: int,
    refresh_hour: int,
) -> bool:
    result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
    if result.get("route_webui_fallback_required"):
        return False
    checked_at_text = str(
        result.get("route_source_checked_at")
        or result.get("browser_verified_at")
        or entry.get("checked_at")
        or ""
    )
    if checked_at_text.endswith("Z"):
        checked_at_text = checked_at_text[:-1] + "+00:00"
    try:
        checked_at = datetime.fromisoformat(checked_at_text)
    except ValueError:
        return False
    if checked_at.tzinfo is None:
        checked_at = checked_at.astimezone()
    latest_refresh_anchor = now.replace(
        hour=refresh_hour,
        minute=0,
        second=0,
        microsecond=0,
    )
    if now < latest_refresh_anchor:
        latest_refresh_anchor -= timedelta(days=1)
    return (
        checked_at >= latest_refresh_anchor
        and now - checked_at <= timedelta(hours=max_age_hours)
    )


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


def cache_origin_matches(cache: dict[str, Any] | None) -> bool:
    """Return whether cached routes were measured from the current origin."""

    cached = (cache or {}).get("origin")
    if not isinstance(cached, dict):
        return False
    try:
        latitude_matches = math.isclose(
            float(cached.get("latitude")),
            float(ORIGIN["latitude"]),
            abs_tol=1e-7,
        )
        longitude_matches = math.isclose(
            float(cached.get("longitude")),
            float(ORIGIN["longitude"]),
            abs_tol=1e-7,
        )
    except (TypeError, ValueError):
        return False
    return (
        str(cached.get("name") or "").strip() == ORIGIN["name"]
        and latitude_matches
        and longitude_matches
    )


def build_cache(
    report: dict[str, Any],
    *,
    api_key: str,
    workers: int,
    existing_cache: dict[str, Any] | None = None,
    max_age_hours: int = DEFAULT_ROUTE_CACHE_HOURS,
    refresh_hour: int = DEFAULT_ROUTE_REFRESH_HOUR,
    reuse_stale_verified_routes: bool = False,
    defer_unverified_routes: bool = False,
    quota_budget: KakaoQuotaBudget | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    jobs = [*(report.get("jobs") or []), *(report.get("logistics_jobs") or [])]
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    if not 0 <= refresh_hour <= 23:
        raise ValueError("route refresh hour must be between 0 and 23")
    origin_matches = cache_origin_matches(existing_cache)
    existing_routes = (
        (existing_cache or {}).get("routes") or {} if origin_matches else {}
    )
    if not isinstance(existing_routes, dict):
        existing_routes = {}
    # Migrate the previous per-recruit cache shape into address-keyed entries.
    if not existing_routes and origin_matches:
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
    cache_hit_keys: set[str] = set()
    stale_reused_keys: set[str] = set()
    for key, group in groups.items():
        if key == "remote:no-commute":
            route_results[key] = {
                "route_status": "not_required",
                "transit_seconds": 0,
                "transit_minutes": 0,
                "transit_type": "REMOTE",
                "transfers": 0,
                "vehicles": [],
                "distance_excluded": False,
                "distance_exclusion_reason": "",
                "route_provider": "none",
                "route_reuse_basis": "remote work; no commute required",
                "route_source_checked_at": now.isoformat(timespec="seconds"),
            }
            continue
        cached = existing_routes.get(key)
        if (
            isinstance(cached, dict)
            and isinstance(cached.get("result"), dict)
            and _cache_entry_is_fresh(
                cached,
                now=now,
                max_age_hours=max_age_hours,
                refresh_hour=refresh_hour,
            )
        ):
            route_results[key] = dict(cached["result"])
            if route_results[key].pop("route_cache_stale", False):
                if route_results[key].get("route_reuse_basis") == (
                    "reused stale browser-verified Kakao route; refresh recommended"
                ):
                    route_results[key]["route_reuse_basis"] = (
                        "browser-verified Kakao route reused within refresh window"
                    )
            cache_hit_keys.add(key)
        elif (
            reuse_stale_verified_routes
            and isinstance(cached, dict)
            and isinstance(cached.get("result"), dict)
            and cached["result"].get("route_status") == "verified"
            and isinstance(cached["result"].get("transit_minutes"), int)
        ):
            route_results[key] = {
                **cached["result"],
                "route_cache_stale": True,
                "route_reuse_basis": (
                    "reused stale browser-verified Kakao route; refresh recommended"
                ),
            }
            cache_hit_keys.add(key)
            stale_reused_keys.add(key)
        else:
            routes_to_fetch[key] = group[0]

    deferred_keys: set[str] = set()
    if routes_to_fetch and not api_key.strip():
        if not defer_unverified_routes:
            raise ValueError(
                "KAKAO_REST_API_KEY is required because one or more commute routes "
                "need a network refresh"
            )
        deferred_keys = set(routes_to_fetch)
        for key in deferred_keys:
            route_results[key] = {
                "route_status": "unverified",
                "distance_excluded": True,
                "distance_exclusion_reason": (
                    "새 근무지는 Kakao Maps web-UI 경로 확인 전까지 거리 제외"
                ),
                "route_reuse_basis": "deferred pending Kakao Maps web-UI pass",
                "route_webui_fallback_required": True,
                "route_fallback_reason": "kakao_rest_key_missing",
            }
        routes_to_fetch = {}

    requested_keys = set(routes_to_fetch)
    quota_before = quota_budget.snapshot()["used"] if quota_budget else None
    request_pacer = KakaoRequestPacer() if routes_to_fetch else None
    if routes_to_fetch:
        with ThreadPoolExecutor(
            max_workers=min(workers, len(routes_to_fetch))
        ) as executor:
            future_to_key = {
                executor.submit(
                    assess_job,
                    job,
                    api_key=api_key,
                    quota_budget=quota_budget,
                    request_pacer=request_pacer,
                ): key
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
                        "route_webui_fallback_required": True,
                        "route_fallback_reason": "kakao_api_error",
                    }

    fallback_keys = {
        key
        for key, result in route_results.items()
        if result.get("route_webui_fallback_required")
    }
    # A failed API refresh must not erase a previously verified route. Reuse it
    # visibly while still keeping the worksite in the WebUI fallback queue.
    for key in fallback_keys:
        cached = existing_routes.get(key)
        cached_result = cached.get("result") if isinstance(cached, dict) else None
        if (
            isinstance(cached_result, dict)
            and cached_result.get("route_status") == "verified"
            and isinstance(cached_result.get("transit_minutes"), int)
        ):
            fallback = route_results[key]
            route_results[key] = {
                **cached_result,
                "route_cache_stale": True,
                "route_webui_fallback_required": True,
                "route_fallback_reason": fallback.get("route_fallback_reason"),
                "route_reuse_basis": (
                    "reused stale verified route while WebUI fallback is pending"
                ),
            }
            stale_reused_keys.add(key)

    results: dict[str, dict[str, Any]] = {}
    for key, group in groups.items():
        for job in group:
            result = {
                **_job_identity(job),
                **route_results[key],
                "route_cache_key": key,
            }
            late_night_profile = result.get("late_night_route_profile")
            if (
                "songpa_late_night" in (job.get("eligibility_lanes") or [])
                and isinstance(late_night_profile, dict)
            ):
                journeys = build_late_night_journeys(
                    job, late_night_profile
                )
                result["late_night_shift_journeys"] = journeys
                exact_outbound_minutes = [
                    leg["minutes"]
                    for journey in journeys
                    for leg in [journey.get("outbound") or {}]
                    if leg.get("future_time_verified")
                    and isinstance(leg.get("minutes"), int)
                ]
                if exact_outbound_minutes:
                    exact_minutes = min(exact_outbound_minutes)
                    excluded = exact_minutes > MAX_TRANSIT_MINUTES
                    result.update(
                        {
                            "transit_seconds": exact_minutes * 60,
                            "transit_minutes": exact_minutes,
                            "transit_type": "NAVER_FUTURE_TIME_BUS_OR_SUBWAY",
                            "landing_url": "https://map.naver.com/",
                            "distance_excluded": excluded,
                            "distance_exclusion_reason": (
                                f"{ORIGIN['name']}에서 근무 시작 전 Naver 미래시간 "
                                f"대중교통 {exact_minutes}분으로 "
                                f"{MAX_TRANSIT_MINUTES}분 이하 기준 미충족"
                                if excluded
                                else ""
                            ),
                            "route_provider": (
                                "Naver Map future-time transit + "
                                "Kakao Maps bicycle fallback"
                            ),
                            "route_reuse_basis": (
                                "exact advertised shift paired with a "
                                "representative future Naver departure"
                            ),
                        }
                    )
            results[str(result["recruit_no"])] = result
    routes = {
        key: {
            "checked_at": (
                existing_routes[key]["checked_at"]
                if key in existing_routes
                and key in cache_hit_keys
                and isinstance(existing_routes[key], dict)
                else now.isoformat(timespec="seconds")
            ),
            "result": route_results[key],
        }
        for key in groups
    }
    reused_provider = (
        str((existing_cache or {}).get("provider") or "").strip()
        if not requested_keys and not deferred_keys
        else ""
    )
    reused_modes = (
        (existing_cache or {}).get("allowed_modes")
        if not requested_keys and not deferred_keys
        else None
    )
    quota_after = quota_budget.snapshot() if quota_budget else None
    actual_api_requests = (
        max(0, int(quota_after["used"]) - int(quota_before))
        if quota_after is not None and quota_before is not None
        else len(requested_keys)
    )
    fallback_jobs = [
        {
            **_job_identity(job),
            "reason": route_results[key].get("route_fallback_reason") or "unknown",
        }
        for key in sorted(fallback_keys)
        for job in groups[key]
    ]
    return {
        "checked_at": now.isoformat(timespec="seconds"),
        "provider": reused_provider or "Kakao Maps public-transit routing",
        "origin": ORIGIN,
        "allowed_modes": (
            reused_modes if isinstance(reused_modes, list) else ["bus", "subway/train"]
        ),
        **(
            {
                "route_time_assumption": existing_cache["route_time_assumption"]
            }
            if not requested_keys and not deferred_keys
            and isinstance(existing_cache, dict)
            and existing_cache.get("route_time_assumption")
            else {}
        ),
        "max_transit_minutes": MAX_TRANSIT_MINUTES,
        "cutoff_operator": "<=",
        "job_count": len(results),
        "unique_route_count": len(groups),
        "route_cache_hit_count": sum(
            len(groups[key]) for key in cache_hit_keys
        ),
        "route_request_count": actual_api_requests,
        "route_stale_reuse_count": sum(
            len(groups[key]) for key in stale_reused_keys
        ),
        "route_deferred_count": sum(len(groups[key]) for key in fallback_keys),
        "webui_fallback_required_count": len(fallback_jobs),
        "webui_fallback_jobs": fallback_jobs,
        **({"kakao_public_transit_quota": quota_after} if quota_after else {}),
        "kakao_request_pacing": {
            "max_requests_per_second": DEFAULT_KAKAO_REQUESTS_PER_SECOND,
            "rate_limit_cooldown_seconds": KAKAO_RATE_LIMIT_COOLDOWN_SECONDS,
        },
        "route_cache_max_age_hours": max_age_hours,
        "route_refresh_hour_local": refresh_hour,
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

    if not cache_origin_matches(cache):
        raise ValueError(
            "transit cache origin differs from the configured origin; "
            "a fresh route lookup is required"
        )

    def reclassify_item(item: dict[str, Any]) -> None:
        if item.get("route_status") == "not_required":
            item["transit_seconds"] = 0
            item["transit_minutes"] = 0
            item["distance_excluded"] = False
            item["distance_exclusion_reason"] = ""
            return
        if item.get("route_status") != "verified":
            item["distance_excluded"] = True
            reason = str(item.get("distance_exclusion_reason") or "")
            if "60분 이내" in reason:
                item["distance_exclusion_reason"] = reason.replace(
                    "60분 이내", f"{max_minutes}분 이하"
                )
            return
        seconds = item.get("transit_seconds")
        if not isinstance(seconds, int):
            item["distance_excluded"] = True
            item["distance_exclusion_reason"] = (
                "캐시에 정확한 대중교통 시간이 없어 재검증 필요"
            )
            return
        minutes = math.ceil(seconds / 60)
        excluded = minutes > max_minutes
        item["transit_minutes"] = minutes
        item["distance_excluded"] = excluded
        item["distance_exclusion_reason"] = (
            f"{ORIGIN['name']}에서 대중교통 최단 {minutes}분으로 "
            f"{max_minutes}분 이하 기준 미충족"
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
    cache["cutoff_operator"] = "<="
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
        "--route-refresh-hour",
        type=int,
        default=DEFAULT_ROUTE_REFRESH_HOUR,
        help="Refresh existing routes once daily after this local hour (0-23).",
    )
    parser.add_argument(
        "--reclassify-cache",
        action="store_true",
        help="Reuse route durations already stored at --output and apply the current cutoff",
    )
    parser.add_argument(
        "--quota-state",
        type=Path,
        default=DEFAULT_KAKAO_QUOTA_STATE,
        help="Persistent local Kakao public-transit free-call ledger.",
    )
    parser.add_argument(
        "--kakao-daily-limit",
        type=int,
        default=DEFAULT_KAKAO_PUBLIC_TRANSIT_DAILY_LIMIT,
        help="Hard API call ceiling; default leaves 100 calls below Kakao's quota.",
    )
    return parser.parse_args()


def env_file_value(path: Path, name: str) -> str:
    """Read one exact value without evaluating or echoing a dotenv file."""

    if not path.exists():
        return ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        if not separator or key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        return value.strip()
    return ""


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
            f"cached commutes; excluded {cache['excluded_count']} at or above the "
            f"strict {cache['max_transit_minutes']}-minute cutoff."
        )
        print(args.output)
        return 0
    api_key = env_file_value(Path(".env"), "KAKAO_REST_API_KEY") or os.environ.get(
        "KAKAO_REST_API_KEY", ""
    ).strip()
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
        refresh_hour=args.route_refresh_hour,
        quota_budget=KakaoQuotaBudget(
            args.quota_state,
            daily_limit=args.kakao_daily_limit,
        ),
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
        f"excluded {cache['excluded_count']} at or above the strict "
        f"{cache['max_transit_minutes']}-minute cutoff."
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
