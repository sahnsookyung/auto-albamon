"""Venue resolution and Kakao commute estimates with shared free-tier guards."""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from tools.albamon_runtime import atomic_write_text
    from tools.albamon_transit import (
        KAKAO_RATE_LIMIT_COOLDOWN_SECONDS,
        KakaoApiUnavailable,
        KakaoQuotaBudget,
        KakaoQuotaExhausted,
        KakaoRequestPacer,
        env_file_value,
        fastest_route,
        kakao_error_code,
        read_kakao_route,
    )
except ModuleNotFoundError:  # Direct CLI execution via tools/.
    from albamon_runtime import atomic_write_text
    from albamon_transit import (
        KAKAO_RATE_LIMIT_COOLDOWN_SECONDS,
        KakaoApiUnavailable,
        KakaoQuotaBudget,
        KakaoQuotaExhausted,
        KakaoRequestPacer,
        env_file_value,
        fastest_route,
        kakao_error_code,
        read_kakao_route,
    )

from .config import (
    LOCATION_CACHE_PATH,
    ROUTE_CACHE_PATH,
    SHARED_KAKAO_QUOTA_PATH,
)
from .models import JobOffer
from .normalize import canonical_employer, compact


KOREA_TIMEZONE = ZoneInfo("Asia/Seoul")
KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
ROUTE_MAX_AGE = timedelta(hours=24)
MAX_CACHE_ENTRIES = 500

# Exact venue/station coordinates avoid repeated place searches for common jobs.
# They are routing anchors, not claims about the employer's legal address.
KNOWN_LOCATIONS: dict[str, tuple[float, float, str]] = {
    "jw메리어트서울": (37.5039, 127.0049, "curated venue routing anchor"),
    "웨스틴조선서울": (37.5641, 126.9805, "curated venue routing anchor"),
    "롯데호텔서울": (37.5653, 126.9809, "curated venue routing anchor"),
    "서울신라호텔": (37.5565, 127.0052, "curated venue routing anchor"),
    "파크하얏트서울": (37.5085, 127.0631, "curated venue routing anchor"),
    "서울드래곤시티": (37.5295, 126.9648, "curated venue routing anchor"),
    "그랜드하얏트서울": (37.5394, 126.9970, "curated venue routing anchor"),
    "페어몬트앰배서더서울호텔": (37.5254, 126.9292, "curated venue routing anchor"),
    "코트야드메리어트서울명동": (37.5660, 126.9824, "curated venue routing anchor"),
    "포포인츠바이쉐라톤서울강남": (37.5236, 127.0364, "curated venue routing anchor"),
    "더블트리바이힐튼서울판교": (37.3942, 127.1121, "curated venue routing anchor"),
    "보코서울명동": (37.5582, 126.9786, "curated venue routing anchor"),
    "세빛섬": (37.5118, 126.9951, "curated venue routing anchor"),
    "리버사이드호텔": (37.5186, 127.0180, "curated venue routing anchor"),
    "봉은사역": (37.5142, 127.0602, "station routing anchor"),
    "회현역": (37.5585, 126.9782, "station routing anchor"),
    "고속터미널역": (37.5048, 127.0049, "station routing anchor"),
    "고속터미널역삼칠구": (37.5048, 127.0049, "station routing anchor"),
    "삼성역": (37.5088, 127.0632, "station routing anchor"),
    "강남구청역": (37.5172, 127.0413, "station routing anchor"),
    "청담역": (37.5194, 127.0539, "station routing anchor"),
    "시청역": (37.5657, 126.9770, "station routing anchor"),
    "을지로입구역": (37.5660, 126.9826, "station routing anchor"),
    "인천공항역12": (37.4475, 126.4525, "station routing anchor"),
    "신논현역": (37.5046, 127.0250, "station routing anchor"),
}

STATION_ALIASES = {
    "고속터미널역삼칠구": "고속터미널역",
    "신논현4번": "신논현역",
}


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "entries": {}}
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return {"version": 1, "entries": {}}
    if not isinstance(payload.get("entries"), dict):
        payload["entries"] = {}
    return payload


def _save_cache(path: Path, payload: dict[str, Any]) -> None:
    entries = payload.get("entries") or {}
    if len(entries) > MAX_CACHE_ENTRIES:
        newest = sorted(
            entries.items(),
            key=lambda item: str((item[1] or {}).get("checked_at") or ""),
            reverse=True,
        )[:MAX_CACHE_ENTRIES]
        payload["entries"] = dict(newest)
    payload["updated_at"] = datetime.now(KOREA_TIMEZONE).isoformat(timespec="seconds")
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
    )


def _query_for_offer(offer: JobOffer) -> str:
    if offer.station:
        station = re.sub(r"\([^)]*\)", "", offer.station).strip()
        alias = STATION_ALIASES.get(compact(station))
        if alias:
            return alias
        station = re.sub(r"\s*\d+번(?:출구)?\s*$", "", station).strip()
        return station if station.endswith("역") else f"{station}역"
    return " ".join(part for part in (offer.employer, offer.area) if part).strip()


def _known_location(query: str, employer: str) -> tuple[float, float, str] | None:
    for value in (query, employer, canonical_employer(employer)):
        key = compact(value)
        if key in KNOWN_LOCATIONS:
            return KNOWN_LOCATIONS[key]
    return None


def _search_place(
    query: str,
    *,
    api_key: str,
    quota: KakaoQuotaBudget,
    pacer: KakaoRequestPacer,
    timeout: int = 15,
) -> tuple[float, float, str]:
    encoded = urllib.parse.urlencode({"query": query, "size": 5})
    request = urllib.request.Request(
        f"{KAKAO_KEYWORD_URL}?{encoded}",
        headers={
            "Authorization": f"KakaoAK {api_key}",
            "User-Agent": "AutoAlbamon-WeekendAgencyReport/1.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            pacer.wait()
            quota.reserve()
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            documents = payload.get("documents") if isinstance(payload, dict) else None
            if not isinstance(documents, list) or not documents:
                raise RuntimeError("Kakao place search returned no candidate")
            candidate = documents[0]
            return (
                float(candidate["y"]),
                float(candidate["x"]),
                f"Kakao place search: {candidate.get('place_name') or query}",
            )
        except urllib.error.HTTPError as exc:
            code = kakao_error_code(exc)
            if code == -10 and attempt == 0:
                pacer.defer(KAKAO_RATE_LIMIT_COOLDOWN_SECONDS)
                continue
            if exc.code == 429 or code in {-10, -11}:
                quota.mark_remote_exhausted(
                    f"quota_{code}" if code is not None else "quota_429"
                )
                raise KakaoQuotaExhausted("Kakao place lookup limit reached") from exc
            if exc.code in {401, 403}:
                quota.mark_remote_exhausted(f"http_{exc.code}_code_{code}")
                raise KakaoApiUnavailable("Kakao place lookup access rejected") from exc
            last_error = exc
            break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.4)
                continue
    raise RuntimeError(f"Kakao place lookup failed: {last_error}")


def _route_summary(payload: dict[str, Any]) -> dict[str, Any]:
    route = fastest_route(payload)
    if route is None:
        raise RuntimeError("Kakao returned no public-transit route")
    properties = route["properties"]
    seconds = int(properties["totalTime"])
    return {
        "transit_minutes": math.ceil(seconds / 60),
        "transit_seconds": seconds,
        "transfers": properties.get("transfers"),
        "fare_won": (properties.get("fare") or {}).get("value"),
        "transit_type": str(properties.get("type") or ""),
    }


Location = tuple[float, float, str]


def _resolve_locations(
    offers: list[JobOffer],
    *,
    api_key: str,
    cache: dict[str, Any],
    quota: KakaoQuotaBudget,
    pacer: KakaoRequestPacer,
    now: datetime,
) -> tuple[dict[str, Location | None], set[str], list[dict[str, str]]]:
    results: dict[str, Location | None] = {}
    unresolved: set[str] = set()
    failures: list[dict[str, str]] = []
    for offer in offers:
        query = _query_for_offer(offer)
        if query in results:
            continue
        known = _known_location(query, offer.employer)
        cached = (cache.get("entries") or {}).get(compact(query))
        cached_valid = isinstance(cached, dict) and all(
            isinstance(cached.get(key), (int, float))
            for key in ("latitude", "longitude")
        )
        if known is not None:
            results[query] = known
        elif cached_valid:
            assert isinstance(cached, dict)
            results[query] = (
                float(cached["latitude"]),
                float(cached["longitude"]),
                str(cached.get("basis") or "cached Kakao place search"),
            )
        elif not api_key:
            results[query] = None
            unresolved.add(query)
        else:
            try:
                results[query] = _search_place(
                    query, api_key=api_key, quota=quota, pacer=pacer
                )
            except Exception as exc:
                results[query] = None
                unresolved.add(query)
                failures.append({"query": query, "error_type": type(exc).__name__})
        location = results[query]
        if location is not None and known is None and not cached_valid:
            latitude, longitude, basis = location
            cache.setdefault("entries", {})[compact(query)] = {
                "query": query,
                "latitude": latitude,
                "longitude": longitude,
                "basis": basis,
                "checked_at": now.isoformat(timespec="seconds"),
            }
    return results, unresolved, failures


def _cache_checked_at(cached: object) -> datetime | None:
    if not isinstance(cached, dict):
        return None
    try:
        checked_at = datetime.fromisoformat(str(cached.get("checked_at") or ""))
    except ValueError:
        return None
    return (
        checked_at.replace(tzinfo=KOREA_TIMEZONE)
        if checked_at.tzinfo is None
        else checked_at
    )


def _resolve_routes(
    locations: set[Location],
    *,
    api_key: str,
    cache: dict[str, Any],
    quota: KakaoQuotaBudget,
    pacer: KakaoRequestPacer,
    now: datetime,
) -> tuple[dict[str, dict[str, Any]], int, list[dict[str, str]]]:
    results: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    stale_reused = 0
    for latitude, longitude, _basis in locations:
        key = f"{latitude:.5f},{longitude:.5f}"
        cached = (cache.get("entries") or {}).get(key)
        checked_at = _cache_checked_at(cached)
        if checked_at is not None and now - checked_at <= ROUTE_MAX_AGE:
            results[key] = {**cached, "status": "verified_cached"}
            continue
        if api_key:
            try:
                summary = _route_summary(
                    read_kakao_route(
                        api_key=api_key,
                        latitude=latitude,
                        longitude=longitude,
                        quota_budget=quota,
                        request_pacer=pacer,
                    )
                )
                results[key] = {
                    **summary,
                    "checked_at": now.isoformat(timespec="seconds"),
                    "status": "verified",
                }
                cache.setdefault("entries", {})[key] = dict(results[key])
                continue
            except Exception as exc:
                failures.append({"route_key": key, "error_type": type(exc).__name__})
        if isinstance(cached, dict) and isinstance(cached.get("transit_minutes"), int):
            results[key] = {**cached, "status": "stale_verified"}
            stale_reused += 1
    return results, stale_reused, failures


def _enrich_offers(
    offers: list[JobOffer],
    locations: dict[str, Location | None],
    routes: dict[str, dict[str, Any]],
) -> list[JobOffer]:
    enriched: list[JobOffer] = []
    for offer in offers:
        location = locations.get(_query_for_offer(offer))
        if location is None:
            enriched.append(offer)
            continue
        latitude, longitude, location_basis = location
        route = routes.get(f"{latitude:.5f},{longitude:.5f}")
        minutes = (
            int(route["transit_minutes"])
            if isinstance(route, dict) and isinstance(route.get("transit_minutes"), int)
            else None
        )
        enriched.append(
            replace(
                offer,
                latitude=latitude,
                longitude=longitude,
                location_basis=location_basis,
                transit_minutes=minutes,
                transit_status=str((route or {}).get("status") or "unverified"),
                transit_basis=(
                    "Kakao public-transit route from 서울특별시 송파구 잠실동 189-4; "
                    "generic schedule, not a future-time guarantee"
                    if route
                    else ""
                ),
            )
        )
    return enriched


def attach_routes(
    offers: list[JobOffer],
    *,
    api_key: str,
    location_cache_path: Path = LOCATION_CACHE_PATH,
    route_cache_path: Path = ROUTE_CACHE_PATH,
    quota_path: Path = SHARED_KAKAO_QUOTA_PATH,
    now: datetime | None = None,
) -> tuple[list[JobOffer], dict[str, Any]]:
    """Attach one cached/verified commute per distinct venue or station."""

    now = now or datetime.now(KOREA_TIMEZONE)
    location_cache = _load_cache(location_cache_path)
    route_cache = _load_cache(route_cache_path)
    quota = KakaoQuotaBudget(quota_path)
    pacer = KakaoRequestPacer()
    before = int(quota.snapshot().get("used") or 0)
    location_results, unresolved, location_failures = _resolve_locations(
        offers,
        api_key=api_key,
        cache=location_cache,
        quota=quota,
        pacer=pacer,
        now=now,
    )
    resolved_locations = {
        location for location in location_results.values() if location is not None
    }
    route_results, stale_reused, route_failures = _resolve_routes(
        resolved_locations,
        api_key=api_key,
        cache=route_cache,
        quota=quota,
        pacer=pacer,
        now=now,
    )
    enriched = _enrich_offers(offers, location_results, route_results)

    _save_cache(location_cache_path, location_cache)
    _save_cache(route_cache_path, route_cache)
    quota_snapshot = quota.snapshot()
    return enriched, {
        "unique_location_count": len(location_results),
        "unresolved_location_count": len(unresolved),
        "location_failures": location_failures,
        "unique_route_count": len(route_results),
        "route_failures": route_failures,
        "stale_route_reuse_count": stale_reused,
        "kakao_calls_this_run": max(0, int(quota_snapshot.get("used") or 0) - before),
        "kakao_quota": quota_snapshot,
    }


def read_api_key(env_file: Path) -> str:
    return env_file_value(env_file, "KAKAO_REST_API_KEY")
