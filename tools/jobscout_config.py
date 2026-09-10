"""Validated, secret-free configuration for JobScout workflows."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG_PATH = ROOT / "config/jobscout.toml"
LOCAL_CONFIG_PATH = ROOT / "config/jobscout.local.toml"
CONFIG_ENV_NAME = "JOBSCOUT_CONFIG"

ALBAMON_SOURCE_OPTIONS = frozenset(
    {"suit", "multi_slot", "evening", "station_safety", "remote", "summer", "urgent"}
)
WORK_PERIOD_OPTIONS = frozenset(
    {"one_day", "under_one_week", "one_week_to_one_month"}
)
AGENCY_SOURCE_OPTIONS = frozenset(
    {"withyou", "huplux", "eumplus", "eoullim", "rutine"}
)
GENDER_OPTIONS = frozenset({"unrestricted", "male", "female"})
SEOUL_DISTRICT_OPTIONS = frozenset(
    {
        "강남구", "강동구", "강북구", "강서구", "관악구", "광진구", "구로구",
        "금천구", "노원구", "도봉구", "동대문구", "동작구", "마포구", "서대문구",
        "서초구", "성동구", "성북구", "송파구", "양천구", "영등포구", "용산구",
        "은평구", "종로구", "중구", "중랑구",
    }
)
EVENING_DISTRICT_OPTIONS = frozenset({"강남구", "서초구", "송파구"})
SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|webhook|password|passwd|secret|token|credential|private[_-]?key)",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
EMAIL_RE = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
PHONE_RE = re.compile(r"(?<!\d)01[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)")
PRIVATE_VALUE_RE = re.compile(
    r"(?:discord(?:app)?\.com/api/webhooks/|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\bKakaoAK\s+[A-Za-z0-9._-]{10,}|"
    r"\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{16,})",
    re.IGNORECASE,
)
PRIVATE_PROFILE_KEY_RE = re.compile(
    r"(?:^|_)(?:name|phone|email|address|birth(?:date)?|resume|account|"
    r"resident(?:_registration)?|government_id|id_number)(?:$|_)",
    re.IGNORECASE,
)

EXPECTED_KEYS = {
    "root": {
        "albamon", "commute", "kakao", "cache", "ranking",
        "weekend_agencies", "dashboard", "applicant",
    },
    "albamon": {
        "saved_search_id", "availability_end_date", "page_size", "workers",
        "enabled_sources", "work_periods", "target_districts",
        "remote_minimum_hourly_won", "requested_age", "requested_gender",
        "evening", "temporary_single_day",
    },
    "albamon.evening": {
        "enabled", "available_from", "available_until", "districts", "stations",
        "station_walk_limit_minutes", "walk_speed_kmh", "walk_route_factor",
    },
    "albamon.temporary_single_day": {"active_dates", "target_dates"},
    "commute": {
        "base_limit_minutes", "route_cache_hours", "route_refresh_hour",
        "default_origin_name", "default_origin_address", "default_origin_latitude",
        "default_origin_longitude", "premium_extension",
    },
    "commute.premium_extension": {
        "districts", "maximum_minutes", "first_tier_minimum_pay_won",
        "first_tier_maximum_pay_won", "first_tier_maximum_minutes",
        "first_tier_won_per_minute", "second_tier_maximum_pay_won",
        "second_tier_won_per_minute",
    },
    "kakao": {"daily_limit", "requests_per_second", "rate_limit_cooldown_seconds"},
    "cache": {
        "excluded_ttl_days", "retention_days", "closing_refresh_hours",
        "eligible_refresh_hours", "applied_refresh_hours", "special_source_hours",
        "weekend_agency_snapshot_hours",
    },
    "ranking": {
        "core_districts", "corridor_districts", "near_core_districts",
        "pay_score_baseline_hourly_won", "combined_tier_a_minimum",
        "combined_tier_b_minimum", "combined_tier_c_minimum",
        "application_tier_a_minimum", "application_tier_b_minimum",
        "application_tier_c_minimum", "district_penalties",
    },
    "ranking.district_penalties": SEOUL_DISTRICT_OPTIONS,
    "weekend_agencies": {
        "search_start", "search_end", "weekends_only",
        "minimum_actual_hourly_won", "enabled_sources",
    },
    "dashboard": {
        "new_match_window_hours", "maximum_exploration_runs",
        "maximum_exploration_days", "exploration_cache_hours",
    },
    "applicant": {"profile_path"},
}


class ConfigurationError(ValueError):
    """Raised before network activity when configuration is unsafe or invalid."""


@dataclass(frozen=True)
class EveningStationSettings:
    name: str
    area_code: str
    line_code: str
    station_code: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class EveningSettings:
    enabled: bool
    available_from: str
    available_until: str
    districts: tuple[str, ...]
    station_definitions: tuple[EveningStationSettings, ...]
    station_walk_limit_minutes: int
    walk_speed_kmh: float
    walk_route_factor: float

    @property
    def stations(self) -> tuple[str, ...]:
        return tuple(station.name for station in self.station_definitions)

    @property
    def station_coordinates(self) -> Mapping[str, tuple[float, float]]:
        return MappingProxyType(
            {
                station.name: (station.latitude, station.longitude)
                for station in self.station_definitions
            }
        )


@dataclass(frozen=True)
class TemporarySingleDaySettings:
    active_dates: frozenset[date]
    target_dates: frozenset[date]


@dataclass(frozen=True)
class AlbamonSettings:
    saved_search_id: int
    availability_end_date: date
    page_size: int
    workers: int
    enabled_sources: tuple[str, ...]
    work_periods: tuple[str, ...]
    target_districts: tuple[str, ...]
    remote_minimum_hourly_won: int
    requested_age: int
    requested_gender: str
    evening: EveningSettings
    temporary_single_day: TemporarySingleDaySettings


@dataclass(frozen=True)
class PremiumExtensionSettings:
    districts: tuple[str, ...]
    maximum_minutes: int
    first_tier_minimum_pay_won: int
    first_tier_maximum_pay_won: int
    first_tier_maximum_minutes: int
    first_tier_won_per_minute: int
    second_tier_maximum_pay_won: int
    second_tier_won_per_minute: int


@dataclass(frozen=True)
class CommuteSettings:
    base_limit_minutes: int
    route_cache_hours: int
    route_refresh_hour: int
    default_origin_name: str
    default_origin_address: str
    default_origin_latitude: float
    default_origin_longitude: float
    premium_extension: PremiumExtensionSettings


@dataclass(frozen=True)
class KakaoSettings:
    daily_limit: int
    requests_per_second: float
    rate_limit_cooldown_seconds: float


@dataclass(frozen=True)
class CacheSettings:
    excluded_ttl_days: int
    retention_days: int
    closing_refresh_hours: int
    eligible_refresh_hours: int
    applied_refresh_hours: int
    special_source_hours: int
    weekend_agency_snapshot_hours: int


@dataclass(frozen=True)
class RankingSettings:
    core_districts: tuple[str, ...]
    corridor_districts: tuple[str, ...]
    near_core_districts: tuple[str, ...]
    district_penalties: Mapping[str, int]
    pay_score_baseline_hourly_won: int
    combined_tier_minimums: tuple[int, int, int]
    application_tier_minimums: tuple[int, int, int]


@dataclass(frozen=True)
class WeekendAgencySettings:
    search_start: date
    search_end: date
    weekends_only: bool
    minimum_actual_hourly_won: int
    enabled_sources: tuple[str, ...]


@dataclass(frozen=True)
class DashboardSettings:
    new_match_window_hours: int
    maximum_exploration_runs: int
    maximum_exploration_days: int
    exploration_cache_hours: int


@dataclass(frozen=True)
class ApplicantSettings:
    profile_path: Path


@dataclass(frozen=True)
class JobScoutSettings:
    albamon: AlbamonSettings
    commute: CommuteSettings
    kakao: KakaoSettings
    cache: CacheSettings
    ranking: RankingSettings
    weekend_agencies: WeekendAgencySettings
    dashboard: DashboardSettings
    applicant: ApplicantSettings
    loaded_paths: tuple[Path, ...]


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"configuration file does not exist: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"invalid TOML in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigurationError(f"configuration must be a TOML table: {path}")
    return payload


def _merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge(dict(result[key]), value)
        else:
            result[key] = value
    return result


def _reject_secret_keys(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            if SECRET_KEY_RE.search(name):
                dotted = ".".join((*path, name))
                raise ConfigurationError(
                    f"secret-like option {dotted!r} is forbidden; use .env instead"
                )
            _reject_secret_keys(child, (*path, name))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_keys(child, (*path, str(index)))


def _reject_secret_values(value: Any, path: tuple[str, ...] = ()) -> None:
    """Prevent credentials from being hidden under an otherwise valid option."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            _reject_secret_values(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_values(child, (*path, str(index)))
    elif isinstance(value, str) and PRIVATE_VALUE_RE.search(value):
        dotted = ".".join(path) or "configuration"
        raise ConfigurationError(
            f"credential-like value in {dotted!r} is forbidden; use .env instead"
        )


def _reject_unknown_keys(payload: Mapping[str, Any]) -> None:
    tables = {
        "root": payload,
        "albamon": _table(payload, "albamon"),
        "albamon.evening": _table(_table(payload, "albamon"), "evening"),
        "albamon.temporary_single_day": _table(
            _table(payload, "albamon"), "temporary_single_day"
        ),
        "commute": _table(payload, "commute"),
        "commute.premium_extension": _table(
            _table(payload, "commute"), "premium_extension"
        ),
        "kakao": _table(payload, "kakao"),
        "cache": _table(payload, "cache"),
        "ranking": _table(payload, "ranking"),
        "ranking.district_penalties": _table(
            _table(payload, "ranking"), "district_penalties"
        ),
        "weekend_agencies": _table(payload, "weekend_agencies"),
        "dashboard": _table(payload, "dashboard"),
        "applicant": _table(payload, "applicant"),
    }
    for name, table in tables.items():
        unknown = sorted(set(table) - EXPECTED_KEYS[name])
        if unknown:
            label = "configuration root" if name == "root" else f"[{name}]"
            raise ConfigurationError(
                f"unknown options in {label}: {', '.join(unknown)}"
            )


def _table(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"missing configuration table [{key}]")
    return value


def _positive_int(table: Mapping[str, Any], key: str, *, minimum: int = 1) -> int:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigurationError(f"{key} must be an integer >= {minimum}")
    return value


def _integer(table: Mapping[str, Any], key: str) -> int:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{key} must be an integer")
    return value


def _positive_float(table: Mapping[str, Any], key: str) -> float:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigurationError(f"{key} must be a positive number")
    return float(value)


def _boolean(table: Mapping[str, Any], key: str) -> bool:
    value = table.get(key)
    if not isinstance(value, bool):
        raise ConfigurationError(f"{key} must be true or false")
    return value


def _date_value(table: Mapping[str, Any], key: str) -> date:
    value = table.get(key)
    if not isinstance(value, date):
        raise ConfigurationError(f"{key} must be an unquoted TOML date (YYYY-MM-DD)")
    return value


def _choice_list(
    table: Mapping[str, Any], key: str, choices: frozenset[str], *, allow_empty: bool = False
) -> tuple[str, ...]:
    value = table.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigurationError(f"{key} must be a list of strings")
    normalized = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
    if not normalized and not allow_empty:
        raise ConfigurationError(f"{key} must not be empty")
    unknown = sorted(set(normalized) - choices)
    if unknown:
        raise ConfigurationError(
            f"{key} contains unsupported values: {', '.join(unknown)}; "
            f"options are {', '.join(sorted(choices))}"
        )
    return normalized


def _choice(table: Mapping[str, Any], key: str, choices: frozenset[str]) -> str:
    value = table.get(key)
    if not isinstance(value, str) or value not in choices:
        raise ConfigurationError(
            f"{key} must be one of {', '.join(sorted(choices))}"
        )
    return value


def _date_list(table: Mapping[str, Any], key: str) -> frozenset[date]:
    value = table.get(key)
    if not isinstance(value, list) or any(not isinstance(item, date) for item in value):
        raise ConfigurationError(f"{key} must be a list of unquoted TOML dates")
    return frozenset(value)


def _station_list(table: Mapping[str, Any], key: str) -> tuple[EveningStationSettings, ...]:
    value = table.get(key)
    expected = {
        "name", "area_code", "line_code", "station_code", "latitude", "longitude"
    }
    if not isinstance(value, list) or not value:
        raise ConfigurationError(f"{key} must be a non-empty list of station tables")
    stations: list[EveningStationSettings] = []
    for index, item in enumerate(value):
        label = f"{key}[{index}]"
        if not isinstance(item, Mapping) or set(item) != expected:
            raise ConfigurationError(
                f"{label} must contain exactly {', '.join(sorted(expected))}"
            )
        name = str(item.get("name") or "").strip()
        raw_codes = (
            item.get("area_code"),
            item.get("line_code"),
            item.get("station_code"),
        )
        if not name or not name.endswith("역"):
            raise ConfigurationError(f"{label}.name must be a station name ending in 역")
        if not all(
            isinstance(value, str) and value.strip().isdigit()
            for value in raw_codes
        ):
            raise ConfigurationError(
                f"{label} Albamon codes must be digit-only quoted strings"
            )
        area_code, line_code, station_code = (
            value.strip() for value in raw_codes if isinstance(value, str)
        )
        latitude = item.get("latitude")
        longitude = item.get("longitude")
        if (
            isinstance(latitude, bool)
            or not isinstance(latitude, (int, float))
            or isinstance(longitude, bool)
            or not isinstance(longitude, (int, float))
            or not 33.0 <= float(latitude) <= 39.5
            or not 124.0 <= float(longitude) <= 132.0
        ):
            raise ConfigurationError(f"{label} coordinates must be within South Korea")
        stations.append(
            EveningStationSettings(
                name=name,
                area_code=area_code,
                line_code=line_code,
                station_code=station_code,
                latitude=float(latitude),
                longitude=float(longitude),
            )
        )
    names = [station.name for station in stations]
    if len(names) != len(set(names)):
        raise ConfigurationError(f"{key} station names must be unique")
    return tuple(stations)


def _path_value(table: Mapping[str, Any], key: str) -> Path:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{key} must be a non-empty path string")
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def load_settings(
    *, base_path: Path = BASE_CONFIG_PATH, override_path: Path | None = None
) -> JobScoutSettings:
    payload = _read_toml(base_path)
    loaded_paths = [base_path]
    if override_path is not None:
        if not override_path.exists():
            raise ConfigurationError(
                f"configuration override does not exist: {override_path}"
            )
        payload = _merge(payload, _read_toml(override_path))
        loaded_paths.append(override_path)
    _reject_secret_keys(payload)
    _reject_secret_values(payload)
    _reject_unknown_keys(payload)

    albamon = _table(payload, "albamon")
    evening = _table(albamon, "evening")
    temporary = _table(albamon, "temporary_single_day")
    commute = _table(payload, "commute")
    premium = _table(commute, "premium_extension")
    kakao = _table(payload, "kakao")
    cache = _table(payload, "cache")
    ranking = _table(payload, "ranking")
    district_penalties = _table(ranking, "district_penalties")
    agencies = _table(payload, "weekend_agencies")
    dashboard = _table(payload, "dashboard")
    applicant = _table(payload, "applicant")

    available_from = str(evening.get("available_from") or "")
    available_until = str(evening.get("available_until") or "")
    if not TIME_RE.fullmatch(available_from) or not TIME_RE.fullmatch(available_until):
        raise ConfigurationError("evening times must use 24-hour HH:MM format")
    availability_end = _date_value(albamon, "availability_end_date")
    search_start = _date_value(agencies, "search_start")
    search_end = _date_value(agencies, "search_end")
    if search_end < search_start:
        raise ConfigurationError("weekend_agencies.search_end precedes search_start")

    daily_limit = _positive_int(kakao, "daily_limit")
    if daily_limit > 900:
        raise ConfigurationError(
            "kakao.daily_limit must be <= 900 for the free-tier safety margin"
        )
    base_limit = _positive_int(commute, "base_limit_minutes")
    premium_max = _positive_int(premium, "maximum_minutes")
    if premium_max < base_limit:
        raise ConfigurationError("premium maximum commute must be >= the base limit")
    first_minimum = _positive_int(premium, "first_tier_minimum_pay_won")
    first_maximum = _positive_int(premium, "first_tier_maximum_pay_won")
    second_maximum = _positive_int(premium, "second_tier_maximum_pay_won")
    if not first_minimum <= first_maximum <= second_maximum:
        raise ConfigurationError("premium pay tiers must be ordered from low to high")
    route_refresh_hour = commute.get("route_refresh_hour")
    if (
        isinstance(route_refresh_hour, bool)
        or not isinstance(route_refresh_hour, int)
        or not 0 <= route_refresh_hour <= 23
    ):
        raise ConfigurationError("commute.route_refresh_hour must be between 0 and 23")

    latitude = _positive_float(commute, "default_origin_latitude")
    longitude = _positive_float(commute, "default_origin_longitude")
    if not 33.0 <= latitude <= 39.5 or not 124.0 <= longitude <= 132.0:
        raise ConfigurationError("default commute origin must be within South Korea")

    saved_search_id = albamon.get("saved_search_id")
    if (
        isinstance(saved_search_id, bool)
        or not isinstance(saved_search_id, int)
        or saved_search_id < 0
    ):
        raise ConfigurationError("albamon.saved_search_id must be a non-negative integer")
    requested_age = albamon.get("requested_age")
    if (
        isinstance(requested_age, bool)
        or not isinstance(requested_age, int)
        or not 0 <= requested_age <= 120
    ):
        raise ConfigurationError("albamon.requested_age must be between 0 and 120")
    page_size = _positive_int(albamon, "page_size")
    workers = _positive_int(albamon, "workers")
    if page_size > 10_000:
        raise ConfigurationError("albamon.page_size must be <= 10000")
    if workers > 32:
        raise ConfigurationError("albamon.workers must be <= 32")
    enabled_albamon_sources = _choice_list(
        albamon, "enabled_sources", ALBAMON_SOURCE_OPTIONS
    )
    work_periods = _choice_list(albamon, "work_periods", WORK_PERIOD_OPTIONS)
    if "multi_slot" in enabled_albamon_sources and "one_day" not in work_periods:
        raise ConfigurationError(
            "albamon.enabled_sources includes multi_slot, so work_periods must include one_day"
        )
    excluded_ttl = _positive_int(cache, "excluded_ttl_days")
    retention_days = _positive_int(cache, "retention_days")
    if retention_days < excluded_ttl:
        raise ConfigurationError("cache.retention_days must be >= excluded_ttl_days")
    combined_tiers = tuple(
        _positive_int(ranking, key, minimum=0)
        for key in (
            "combined_tier_a_minimum",
            "combined_tier_b_minimum",
            "combined_tier_c_minimum",
        )
    )
    application_tiers = tuple(
        _positive_int(ranking, key, minimum=0)
        for key in (
            "application_tier_a_minimum",
            "application_tier_b_minimum",
            "application_tier_c_minimum",
        )
    )
    if not (combined_tiers[0] > combined_tiers[1] > combined_tiers[2]):
        raise ConfigurationError("combined ranking tier minimums must descend A > B > C")
    if not (application_tiers[0] > application_tiers[1] > application_tiers[2]):
        raise ConfigurationError("application ranking tier minimums must descend A > B > C")
    zone_groups = (
        _choice_list(ranking, "core_districts", SEOUL_DISTRICT_OPTIONS),
        _choice_list(ranking, "corridor_districts", SEOUL_DISTRICT_OPTIONS),
        _choice_list(ranking, "near_core_districts", SEOUL_DISTRICT_OPTIONS),
    )
    zone_members = [district for group in zone_groups for district in group]
    if len(zone_members) != len(set(zone_members)):
        raise ConfigurationError("ranking district groups must not overlap")
    origin_name = str(commute.get("default_origin_name") or "").strip()
    origin_address = str(commute.get("default_origin_address") or "").strip()
    if not origin_name or not origin_address:
        raise ConfigurationError("default commute origin name and address are required")

    return JobScoutSettings(
        albamon=AlbamonSettings(
            saved_search_id=saved_search_id,
            availability_end_date=availability_end,
            page_size=page_size,
            workers=workers,
            enabled_sources=enabled_albamon_sources,
            work_periods=work_periods,
            target_districts=_choice_list(albamon, "target_districts", SEOUL_DISTRICT_OPTIONS),
            remote_minimum_hourly_won=_positive_int(albamon, "remote_minimum_hourly_won"),
            requested_age=requested_age,
            requested_gender=_choice(albamon, "requested_gender", GENDER_OPTIONS),
            evening=EveningSettings(
                enabled=_boolean(evening, "enabled"),
                available_from=available_from,
                available_until=available_until,
                districts=_choice_list(evening, "districts", EVENING_DISTRICT_OPTIONS),
                station_definitions=_station_list(evening, "stations"),
                station_walk_limit_minutes=_positive_int(evening, "station_walk_limit_minutes"),
                walk_speed_kmh=_positive_float(evening, "walk_speed_kmh"),
                walk_route_factor=_positive_float(evening, "walk_route_factor"),
            ),
            temporary_single_day=TemporarySingleDaySettings(
                active_dates=_date_list(temporary, "active_dates"),
                target_dates=_date_list(temporary, "target_dates"),
            ),
        ),
        commute=CommuteSettings(
            base_limit_minutes=base_limit,
            route_cache_hours=_positive_int(commute, "route_cache_hours"),
            route_refresh_hour=route_refresh_hour,
            default_origin_name=origin_name,
            default_origin_address=origin_address,
            default_origin_latitude=latitude,
            default_origin_longitude=longitude,
            premium_extension=PremiumExtensionSettings(
                districts=_choice_list(premium, "districts", SEOUL_DISTRICT_OPTIONS),
                maximum_minutes=premium_max,
                first_tier_minimum_pay_won=first_minimum,
                first_tier_maximum_pay_won=first_maximum,
                first_tier_maximum_minutes=_positive_int(premium, "first_tier_maximum_minutes"),
                first_tier_won_per_minute=_positive_int(premium, "first_tier_won_per_minute"),
                second_tier_maximum_pay_won=second_maximum,
                second_tier_won_per_minute=_positive_int(premium, "second_tier_won_per_minute"),
            ),
        ),
        kakao=KakaoSettings(
            daily_limit=daily_limit,
            requests_per_second=_positive_float(kakao, "requests_per_second"),
            rate_limit_cooldown_seconds=_positive_float(kakao, "rate_limit_cooldown_seconds"),
        ),
        cache=CacheSettings(
            excluded_ttl_days=excluded_ttl,
            retention_days=retention_days,
            closing_refresh_hours=_positive_int(cache, "closing_refresh_hours"),
            eligible_refresh_hours=_positive_int(cache, "eligible_refresh_hours"),
            applied_refresh_hours=_positive_int(cache, "applied_refresh_hours"),
            special_source_hours=_positive_int(cache, "special_source_hours"),
            weekend_agency_snapshot_hours=_positive_int(cache, "weekend_agency_snapshot_hours"),
        ),
        ranking=RankingSettings(
            core_districts=zone_groups[0],
            corridor_districts=zone_groups[1],
            near_core_districts=zone_groups[2],
            district_penalties=MappingProxyType(
                {key: _integer(district_penalties, key) for key in district_penalties}
            ),
            pay_score_baseline_hourly_won=_positive_int(
                ranking, "pay_score_baseline_hourly_won"
            ),
            combined_tier_minimums=combined_tiers,
            application_tier_minimums=application_tiers,
        ),
        weekend_agencies=WeekendAgencySettings(
            search_start=search_start,
            search_end=search_end,
            weekends_only=_boolean(agencies, "weekends_only"),
            minimum_actual_hourly_won=_positive_int(agencies, "minimum_actual_hourly_won"),
            enabled_sources=_choice_list(agencies, "enabled_sources", AGENCY_SOURCE_OPTIONS),
        ),
        dashboard=DashboardSettings(
            new_match_window_hours=_positive_int(dashboard, "new_match_window_hours"),
            maximum_exploration_runs=_positive_int(dashboard, "maximum_exploration_runs"),
            maximum_exploration_days=_positive_int(dashboard, "maximum_exploration_days"),
            exploration_cache_hours=_positive_int(dashboard, "exploration_cache_hours"),
        ),
        applicant=ApplicantSettings(profile_path=_path_value(applicant, "profile_path")),
        loaded_paths=tuple(loaded_paths),
    )


@lru_cache(maxsize=1)
def get_settings() -> JobScoutSettings:
    explicit = os.environ.get(CONFIG_ENV_NAME, "").strip()
    if explicit:
        override = Path(explicit).expanduser()
    else:
        override = LOCAL_CONFIG_PATH if LOCAL_CONFIG_PATH.exists() else None
    return load_settings(base_path=BASE_CONFIG_PATH, override_path=override)


SETTINGS = get_settings()


def load_applicant_profile(path: Path | None = None) -> dict[str, dict[str, str]]:
    """Load verified résumé facts without permitting contact data or credentials."""

    selected = path or SETTINGS.applicant.profile_path
    if not selected.exists():
        return {}
    payload = _read_toml(selected)
    _reject_secret_keys(payload)
    facts = payload.get("facts")
    if not isinstance(facts, Mapping):
        raise ConfigurationError("applicant profile must contain a [facts] table")
    if set(payload) != {"facts"}:
        raise ConfigurationError("applicant profile supports only [facts.*] tables")
    normalized: dict[str, dict[str, str]] = {}
    for raw_key, raw_value in facts.items():
        key = str(raw_key).strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", key):
            raise ConfigurationError(f"invalid applicant fact identifier: {key!r}")
        if PRIVATE_PROFILE_KEY_RE.search(key):
            raise ConfigurationError(
                f"facts.{key} is contact or identity data and is not allowed"
            )
        if not isinstance(raw_value, Mapping) or set(raw_value) != {"fact", "source"}:
            raise ConfigurationError(
                f"facts.{key} must contain exactly fact and source"
            )
        fact = " ".join(str(raw_value.get("fact") or "").split())
        source = " ".join(str(raw_value.get("source") or "").split())
        if not fact or len(fact) > 240 or not source or len(source) > 120:
            raise ConfigurationError(f"facts.{key} has an invalid fact or source length")
        combined = f"{fact} {source}"
        if (
            EMAIL_RE.search(combined)
            or PHONE_RE.search(combined)
            or PRIVATE_VALUE_RE.search(combined)
        ):
            raise ConfigurationError(
                f"facts.{key} appears to contain contact data or a credential"
            )
        normalized[key] = {"fact": fact, "source": source}
    return normalized
