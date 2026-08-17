"""Canonical hotel-staffing agency catalog and source-name aliases."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class HotelAgency:
    """Stable domain metadata for one hotel-staffing agency."""

    agency_id: str
    name: str
    english_name: str | None
    featured_hotels: tuple[str, ...]
    specialties: tuple[str, ...]
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.agency_id or not self.name:
            raise ValueError("agency ID and name are required")
    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-safe public representation used by reports."""

        return {
            "agency_id": self.agency_id,
            "name": self.name,
            "english_name": self.english_name,
            "featured_hotels": list(self.featured_hotels),
            "specialties": list(self.specialties),
            "aliases": list(self.aliases),
        }


HOTEL_AGENCIES = (
    HotelAgency(
        agency_id="dayagency",
        name="데이에이전시",
        english_name=None,
        featured_hotels=(
            "로카우스",
            "코엑스 인터컨티넨탈",
            "그랜드 인터컨티넨탈",
            "아모리스",
        ),
        specialties=(),
        aliases=("day_agency", "day agency", "데이 에이전시"),
    ),
    HotelAgency(
        agency_id="huplux",
        name="후플럭스",
        english_name="HUPLUX",
        featured_hotels=("신라호텔", "조선호텔", "서울드래곤시티", "리버사이드"),
        specialties=(),
        aliases=("hup lux", "휴플럭스"),
    ),
    HotelAgency(
        agency_id="eumplus",
        name="이음플러스",
        english_name=None,
        featured_hotels=("JW메리어트", "콘래드", "서울드래곤시티", "리버사이드"),
        specialties=(),
        aliases=("eum_plus", "eum plus", "이음 플러스"),
    ),
    HotelAgency(
        agency_id="withyou",
        name="위드유",
        english_name=None,
        featured_hotels=(
            "그랜드하얏트",
            "롯데호텔서울",
            "웨스틴조선",
            "페어몬트",
            "힐튼/그래비티 판교",
        ),
        specialties=(),
        aliases=("with_you", "with you", "위드 유"),
    ),
    HotelAgency(
        agency_id="htservice",
        name="호텔서비스",
        english_name="HTService",
        featured_hotels=("워커힐",),
        specialties=("연회", "뷔페", "주방"),
        aliases=("hotelservice", "hotel service", "ht service", "HT서비스"),
    ),
    HotelAgency(
        agency_id="eoullim",
        name="어울림",
        english_name=None,
        featured_hotels=(),
        specialties=("호텔", "웨딩/컨벤션", "단기 알바"),
        aliases=("oulim", "어울림에이전시", "eoullim agency"),
    ),
    HotelAgency(
        agency_id="rutine",
        name="루틴",
        english_name="RUTINE",
        featured_hotels=(
            "포시즌스 호텔",
            "그랜드 인터컨티넨탈 파르나스",
            "앰배서더 서울 풀만",
            "호텔 리베라",
            "로카우스",
        ),
        specialties=("호텔", "컨벤션", "일일 알바"),
        aliases=("rutin", "routine", "루틴에이전시"),
    ),
)


def _alias_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold(), flags=re.UNICODE)


def _build_alias_index() -> MappingProxyType[str, HotelAgency]:
    index: dict[str, HotelAgency] = {}
    for agency in HOTEL_AGENCIES:
        values = (
            agency.agency_id,
            agency.name,
            agency.english_name or "",
            *agency.aliases,
        )
        for value in values:
            key = _alias_key(value)
            if not key:
                continue
            previous = index.setdefault(key, agency)
            if previous is not agency:
                raise ValueError(f"duplicate hotel-agency alias: {value}")
    return MappingProxyType(index)


_AGENCIES_BY_ALIAS = _build_alias_index()


AGENCY_SOURCE_COVERAGE = MappingProxyType(
    {
        "dayagency": {
            "status": "catalog_only_auth_required",
            "reason": "Exact dated shifts are behind a member application flow.",
        },
        "huplux": {"status": "configured_public_source", "reason": "Public list"},
        "eumplus": {"status": "configured_public_source", "reason": "Public list"},
        "withyou": {"status": "configured_public_source", "reason": "Public list"},
        "htservice": {
            "status": "catalog_only_low_yield",
            "reason": "Disabled: target jobs were below the base-pay threshold; source transport is HTTP.",
        },
        "eoullim": {
            "status": "configured_public_source",
            "reason": "Public list; job details require login.",
        },
        "rutine": {
            "status": "configured_public_source",
            "reason": "Public dated shift table.",
        },
    }
)


def resolve_hotel_agency(value: str) -> HotelAgency | None:
    """Resolve a source ID, display name, or known spelling to its agency."""

    return _AGENCIES_BY_ALIAS.get(_alias_key(str(value or "")))


def hotel_agency_label(value: str) -> str:
    """Return the canonical Korean label while preserving unknown source IDs."""

    agency = resolve_hotel_agency(value)
    return agency.name if agency is not None else value


def hotel_agency_catalog_payload() -> list[dict[str, Any]]:
    """Return a fresh JSON-safe copy of the ordered agency catalog."""

    return [agency.as_dict() for agency in HOTEL_AGENCIES]


def hotel_agency_source_coverage_payload() -> dict[str, dict[str, str]]:
    """Return static collection coverage without claiming a fetch succeeded."""

    return {
        agency_id: dict(coverage)
        for agency_id, coverage in AGENCY_SOURCE_COVERAGE.items()
    }
