"""Conservative shift parsing, normalization, and cross-agency grouping."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import Iterable

from .config import MIN_ACTUAL_HOURLY_WON, SEARCH_DATES
from .models import JobOffer
from .pay import TIME_RE


EMPLOYER_ALIASES = {
    "jw메리어트호텔": "JW메리어트 서울",
    "jw메리어트서울": "JW메리어트 서울",
    "jw메리어트서울호텔": "JW메리어트 서울",
    "웨스틴조선호텔서울": "웨스틴 조선 서울",
    "웨스틴조선호텔": "웨스틴 조선 서울",
    "조선호텔": "웨스틴 조선 서울",
    "파크하얏트서울": "파크 하얏트 서울",
    "파크하얏트호텔": "파크 하얏트 서울",
    "신라호텔": "서울 신라호텔",
    "신라야간": "서울 신라호텔",
    "서울신라호텔": "서울 신라호텔",
    "호텔롯데서울": "롯데호텔 서울",
    "롯데호텔서울": "롯데호텔 서울",
    "서울드래곤시티": "서울 드래곤시티",
    "보코서울명동호텔": "보코 서울 명동",
    "조선팰리스서울강남": "조선 팰리스 서울강남",
}

DEPARTMENT_TOKENS = (
    "1914",
    "콘스탄스",
    "홍연",
    "아리아",
    "팔선",
    "파크뷰",
    "라세느",
    "차르",
    "데메테르",
    "더마켓키친",
    "스펙트럼",
    "웨이루",
    "모모카페",
    "플라워",
    "연회장",
    "연회",
)


def compact(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", value.lower())


def canonical_employer(value: str) -> str:
    clean = re.sub(r"^\s*\[서울\]\s*", "", value).strip()
    return EMPLOYER_ALIASES.get(compact(clean), clean)


def role_category(text: str) -> str:
    value = compact(text)
    if any(token in value for token in ("설거지", "세척", "기물", "백사이드")):
        return "dishwashing_back_of_house"
    if any(token in value for token in ("주방", "조리", "키친")):
        return "kitchen_assistant"
    if any(token in value for token in ("하우스키핑", "객실", "룸메이드")):
        return "housekeeping"
    if any(token in value for token in ("홀", "서빙", "연회", "뷔페", "라운지", "서비스")):
        return "guest_service"
    if "플라워" in value:
        return "retail_flower"
    return "other"


def department_key(text: str) -> str:
    value = compact(text)
    return next((token for token in DEPARTMENT_TOKENS if token in value), "generic")


def extract_shifts(text: str) -> list[tuple[str, str]]:
    return [
        (f"{int(match.group(1)):02d}:{match.group(2)}", f"{int(match.group(3)):02d}:{match.group(4)}")
        for match in TIME_RE.finditer(text)
    ]


def offer_is_actionable(offer: JobOffer) -> bool:
    return (
        offer.work_date in SEARCH_DATES
        and offer.work_date.weekday() >= 5
        and offer.status == "open"
        and isinstance(offer.pay.actual_hourly_won, int)
        and offer.pay.actual_hourly_won >= MIN_ACTUAL_HOURLY_WON
        and bool(offer.shift_start and offer.shift_end)
    )


def semantic_group_key(offer: JobOffer) -> str:
    material = "|".join(
        (
            compact(canonical_employer(offer.employer)),
            offer.work_date.isoformat(),
            offer.shift_start,
            offer.shift_end,
            offer.role_category,
            department_key(offer.title),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def with_normalized_employer(offer: JobOffer) -> JobOffer:
    return replace(offer, employer=canonical_employer(offer.employer))


def aggregate_offers(offers: Iterable[JobOffer]) -> list[dict[str, object]]:
    material_groups: dict[str, list[JobOffer]] = {}
    for offer in offers:
        normalized = with_normalized_employer(offer)
        material_groups.setdefault(semantic_group_key(normalized), []).append(normalized)

    grouped: dict[str, list[JobOffer]] = {}
    for material_key, variants in material_groups.items():
        agencies = {item.agency for item in variants}
        if len(agencies) > 1 or len(variants) == 1:
            grouped[material_key] = variants
            continue
        # The user asked to compare duplicates between agencies. Preserve two
        # same-agency postings as distinct choices even when their coarse role
        # and department labels happen to match.
        for item in variants:
            listing_id = item.source_id.rsplit(":", 1)[0]
            unique_material = f"{material_key}|{item.agency}|{listing_id}"
            unique_key = hashlib.sha256(unique_material.encode("utf-8")).hexdigest()[:20]
            grouped.setdefault(unique_key, []).append(item)

    results: list[dict[str, object]] = []
    for group_id, variants in grouped.items():
        ordered = sorted(
            variants,
            key=lambda item: (
                -(item.pay.actual_hourly_won or 0),
                item.transit_minutes if item.transit_minutes is not None else 10_000,
                item.agency,
            ),
        )
        best = ordered[0]
        highest_pay = best.pay.actual_hourly_won
        highest_paying_agencies = sorted(
            {
                item.agency
                for item in variants
                if item.pay.actual_hourly_won == highest_pay
            }
        )
        commute_values = [
            item.transit_minutes
            for item in variants
            if isinstance(item.transit_minutes, int)
        ]
        agencies = sorted({item.agency for item in variants})
        results.append(
            {
                "group_id": group_id,
                "employer": best.employer,
                "work_date": best.work_date.isoformat(),
                "weekday": "토" if best.work_date.weekday() == 5 else "일",
                "shift": best.shift,
                "shift_start": best.shift_start,
                "shift_end": best.shift_end,
                "role_category": best.role_category,
                "department_key": department_key(best.title),
                "best_actual_hourly_won": highest_pay,
                "best_agency": (
                    highest_paying_agencies[0]
                    if len(highest_paying_agencies) == 1
                    else "tie"
                ),
                "highest_paying_agencies": highest_paying_agencies,
                "best_weekly_allowance_hourly_won": max(
                    (
                        item.pay.weekly_allowance_hourly_won
                        for item in variants
                        if isinstance(item.pay.weekly_allowance_hourly_won, int)
                    ),
                    default=None,
                ),
                "transit_minutes": min(commute_values) if commute_values else None,
                "transit_status": (
                    "verified" if commute_values else "unverified"
                ),
                "station": next((item.station for item in variants if item.station), ""),
                "area": next((item.area for item in variants if item.area), ""),
                "source_count": len(agencies),
                "agencies": agencies,
                "cross_agency_duplicate": len(agencies) > 1,
                "offers": [item.as_dict() for item in ordered],
            }
        )

    results.sort(
        key=lambda item: (
            -int(item["best_actual_hourly_won"] or 0),
            int(item["transit_minutes"])
            if isinstance(item.get("transit_minutes"), int)
            else 10_000,
            str(item["work_date"]),
            str(item["employer"]),
            str(item["shift"]),
        )
    )
    for rank, item in enumerate(results, start=1):
        item["rank"] = rank
    return results
