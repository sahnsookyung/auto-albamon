"""Hotel-level aggregation and bounded Discord rendering.

The source report intentionally preserves exact shift/provider rows.  Discord
needs a different projection: one overview row per hotel, plus detailed blocks
that retain every distinct shift while collapsing repeated dates and postings.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Iterable

from .config import SOURCE_LABELS


DISCORD_MESSAGE_LIMIT = 2_000
ROLE_LABELS = {
    "guest_service": "홀·연회·고객서비스",
    "dishwashing_back_of_house": "기물·세척",
    "kitchen_assistant": "주방보조",
    "housekeeping": "하우스키핑",
    "retail_flower": "플라워",
    "other": "기타·공고확인",
}
SOURCE_SHORT_LABELS = {
    "withyou": "위드유",
    "huplux": "휴플럭스",
    "eumplus": "이음",
}


@dataclass(frozen=True)
class DigestPage:
    message: str
    hotel_ids: tuple[str, ...]


def _safe_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    text = text.replace("@", "@\u200b").replace("```", "''' ")
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _won(value: Any) -> str:
    return f"₩{value:,}" if isinstance(value, int) else "—"


def _date_label(value: str) -> str:
    parts = value.split("-")
    if len(parts) == 3:
        return f"{int(parts[1])}/{int(parts[2])}"
    return value


def _source_link(value: Any) -> str:
    text = str(value or "").strip()
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "링크 미확인"
    return f"<{text}>"


def hotel_identity(employer: Any) -> str:
    normalized = re.sub(r"[^0-9a-z가-힣]", "", str(employer or "").lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def _provider_summary(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for offer in offers:
        grouped.setdefault(str(offer.get("agency") or "unknown"), []).append(offer)
    summaries: list[dict[str, Any]] = []
    for agency, variants in grouped.items():
        def pay_value(item: dict[str, Any], name: str) -> int:
            value = (item.get("pay") or {}).get(name)
            return int(value) if isinstance(value, int) else -1

        best = max(
            variants,
            key=lambda item: (
                pay_value(item, "actual_hourly_won"),
                pay_value(item, "weekly_allowance_hourly_won"),
                str(item.get("source_url") or ""),
            ),
        )
        actual_values = [
            pay_value(item, "actual_hourly_won")
            for item in variants
            if pay_value(item, "actual_hourly_won") >= 0
        ]
        weekly_values = [
            pay_value(item, "weekly_allowance_hourly_won")
            for item in variants
            if pay_value(item, "weekly_allowance_hourly_won") >= 0
        ]
        summaries.append(
            {
                "agency": agency,
                "label": SOURCE_LABELS.get(agency, agency),
                "actual_hourly_max": max(actual_values, default=None),
                "weekly_allowance_hourly_max": max(weekly_values, default=None),
                "offer_count": len(variants),
                "source_count": len(
                    {str(item.get("source_url") or "") for item in variants}
                ),
                "representative_url": best.get("source_url"),
            }
        )
    summaries.sort(
        key=lambda item: (
            -int(item.get("actual_hourly_max") or 0),
            str(item.get("label") or ""),
        )
    )
    return summaries


def _shift_options(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Rate is first resolved for each exact date+shift.  Dates are combined only
    # when shift, best actual rate, conditional weekly rate, and best providers
    # all match, avoiding a misleading implication that one day's maximum rate
    # is available on every other date.
    resolved: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("work_date") or ""), str(row.get("shift") or ""))
        candidate = {
            "work_date": key[0],
            "shift": key[1],
            "actual_hourly_max": row.get("best_actual_hourly_won"),
            "weekly_allowance_hourly_max": row.get(
                "best_weekly_allowance_hourly_won"
            ),
            "best_agencies": tuple(row.get("highest_paying_agencies") or ()),
        }
        prior = resolved.get(key)
        if prior is None or int(candidate["actual_hourly_max"] or 0) > int(
            prior["actual_hourly_max"] or 0
        ):
            resolved[key] = candidate
        elif candidate["actual_hourly_max"] == prior["actual_hourly_max"]:
            weekly_values = [
                value
                for value in (
                    prior.get("weekly_allowance_hourly_max"),
                    candidate.get("weekly_allowance_hourly_max"),
                )
                if isinstance(value, int)
            ]
            prior["weekly_allowance_hourly_max"] = max(
                weekly_values, default=None
            )
            prior["best_agencies"] = tuple(
                sorted(
                    {
                        *prior.get("best_agencies", ()),
                        *candidate.get("best_agencies", ()),
                    }
                )
            )

    collapsed: dict[tuple[Any, ...], set[str]] = {}
    for item in resolved.values():
        key = (
            item["shift"],
            item["actual_hourly_max"],
            item["weekly_allowance_hourly_max"],
            item["best_agencies"],
        )
        collapsed.setdefault(key, set()).add(str(item["work_date"]))
    result = [
        {
            "shift": key[0],
            "actual_hourly_max": key[1],
            "weekly_allowance_hourly_max": key[2],
            "best_agencies": list(key[3]),
            "dates": sorted(dates),
        }
        for key, dates in collapsed.items()
    ]
    result.sort(
        key=lambda item: (
            str(item["shift"]),
            -int(item.get("actual_hourly_max") or 0),
            item["dates"],
        )
    )
    return result


def _is_daytime_shift(value: Any) -> bool:
    match = re.fullmatch(r"(\d{2}):(\d{2})~(\d{2}):(\d{2})", str(value or ""))
    if not match:
        return False
    start = int(match.group(1)) * 60 + int(match.group(2))
    end = int(match.group(3)) * 60 + int(match.group(4))
    return 6 * 60 <= start < end <= 20 * 60


def aggregate_hotels(jobs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for job in jobs:
        employer = str(job.get("employer") or "미확인 호텔")
        grouped.setdefault(employer, []).append(job)

    hotels: list[dict[str, Any]] = []
    for employer, rows in grouped.items():
        offers = [
            offer
            for row in rows
            for offer in row.get("offers") or []
            if isinstance(offer, dict)
        ]
        providers = _provider_summary(offers)
        actual_values = [
            int(row["best_actual_hourly_won"])
            for row in rows
            if isinstance(row.get("best_actual_hourly_won"), int)
        ]
        weekly_values = [
            int(row["best_weekly_allowance_hourly_won"])
            for row in rows
            if isinstance(row.get("best_weekly_allowance_hourly_won"), int)
        ]
        commute_values = [
            int(row["transit_minutes"])
            for row in rows
            if isinstance(row.get("transit_minutes"), int)
        ]
        max_actual = max(actual_values, default=None)
        best_agencies = sorted(
            str(item["agency"])
            for item in providers
            if item.get("actual_hourly_max") == max_actual
        )
        shift_options = _shift_options(rows)
        daytime_values = [
            int(option["actual_hourly_max"])
            for option in shift_options
            if _is_daytime_shift(option.get("shift"))
            and isinstance(option.get("actual_hourly_max"), int)
        ]
        dates = sorted({str(row.get("work_date") or "") for row in rows})
        hotels.append(
            {
                "hotel_id": hotel_identity(employer),
                "employer": employer,
                "actual_hourly_max": max_actual,
                "actual_hourly_min": min(actual_values, default=None),
                "daytime_actual_hourly_max": max(daytime_values, default=None),
                "weekly_allowance_hourly_max": max(weekly_values, default=None),
                "transit_minutes": min(commute_values, default=None),
                "dates": dates,
                "date_count": len(dates),
                "shift_count": len({str(row.get("shift") or "") for row in rows}),
                "job_count": len(rows),
                "offer_count": len(offers),
                "cross_agency_duplicate_count": sum(
                    bool(row.get("cross_agency_duplicate")) for row in rows
                ),
                "best_agencies": best_agencies,
                "providers": providers,
                "roles": sorted(
                    {str(row.get("role_category") or "other") for row in rows}
                ),
                "station": next(
                    (str(row.get("station")) for row in rows if row.get("station")),
                    "",
                ),
                "area": next(
                    (str(row.get("area")) for row in rows if row.get("area")),
                    "",
                ),
                "shift_options": shift_options,
                "source_snapshot_stale": any(
                    (offer.get("metadata") or {}).get("source_snapshot_stale")
                    for offer in offers
                ),
            }
        )

    hotels.sort(
        key=lambda item: (
            -int(item.get("actual_hourly_max") or 0),
            int(item["transit_minutes"])
            if isinstance(item.get("transit_minutes"), int)
            else 10_000,
            str(item["employer"]),
        )
    )
    for rank, hotel in enumerate(hotels, start=1):
        hotel["rank"] = rank
    return hotels


def hotel_fingerprint(hotel: dict[str, Any]) -> str:
    material = {
        key: value
        for key, value in hotel.items()
        if key not in {"rank", "source_snapshot_stale"}
    }
    rendered = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def digest_fingerprint(hotels: list[dict[str, Any]]) -> str:
    material = [
        (str(hotel["hotel_id"]), hotel_fingerprint(hotel)) for hotel in hotels
    ]
    return hashlib.sha256(
        json.dumps(material, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _overview_row(hotel: dict[str, Any]) -> str:
    best = "/".join(
        SOURCE_SHORT_LABELS.get(value, value)
        for value in hotel.get("best_agencies") or []
    ) or "—"
    commute = (
        str(hotel["transit_minutes"])
        if isinstance(hotel.get("transit_minutes"), int)
        else "—"
    )
    return (
        f"{int(hotel['rank']):02d} | {_safe_text(hotel['employer'], limit=28)} | "
        f"{_won(hotel.get('actual_hourly_max'))}/"
        f"{_won(hotel.get('daytime_actual_hourly_max'))} | "
        f"{_won(hotel.get('weekly_allowance_hourly_max'))} | {commute}분 | "
        f"{hotel['date_count']}일/{hotel['shift_count']}T/{hotel['job_count']}건 | {best}"
    )


def _hotel_block(hotel: dict[str, Any], *, change_kind: str | None = None) -> str:
    change = "🆕 " if change_kind == "new" else "🔄 " if change_kind else ""
    actual_range = _won(hotel.get("actual_hourly_max"))
    if (
        isinstance(hotel.get("actual_hourly_min"), int)
        and hotel.get("actual_hourly_min") != hotel.get("actual_hourly_max")
    ):
        actual_range = (
            f"{_won(hotel.get('actual_hourly_min'))}–"
            f"{_won(hotel.get('actual_hourly_max'))}"
        )
    commute = (
        f"{hotel['transit_minutes']}분"
        if isinstance(hotel.get("transit_minutes"), int)
        else "미검증"
    )
    role_labels = ", ".join(
        ROLE_LABELS.get(role, role) for role in hotel.get("roles") or []
    )
    lines = [
        f"{change}**{hotel['rank']}. {_safe_text(hotel['employer'], limit=100)}**",
        f"실제 {actual_range} · 주간 최대 {_won(hotel.get('daytime_actual_hourly_max'))} "
        f"· 주휴* 최대 {_won(hotel.get('weekly_allowance_hourly_max'))} "
        f"· 통근 {commute} · {hotel['date_count']}일/{hotel['shift_count']}시간대/"
        f"{hotel['job_count']}선택지",
        f"업무 {role_labels or '공고확인'} · 위치 "
        f"{_safe_text(hotel.get('station') or hotel.get('area') or '미표기', limit=100)}",
        "시간별 최고 실제시급:",
    ]
    for option in hotel.get("shift_options") or []:
        dates = ",".join(_date_label(value) for value in option.get("dates") or [])
        agencies = "/".join(
            SOURCE_SHORT_LABELS.get(value, value)
            for value in option.get("best_agencies") or []
        ) or "미확인"
        weekly = (
            f" · 주휴* {_won(option.get('weekly_allowance_hourly_max'))}"
            if isinstance(option.get("weekly_allowance_hourly_max"), int)
            else ""
        )
        lines.append(
            f"• `{option.get('shift')}` · **{_won(option.get('actual_hourly_max'))}**"
            f"{weekly} · {dates} · {agencies}"
        )
    lines.append("업체별 최고가/대표공고:")
    for provider in hotel.get("providers") or []:
        weekly = (
            _won(provider.get("weekly_allowance_hourly_max"))
            if isinstance(provider.get("weekly_allowance_hourly_max"), int)
            else "—"
        )
        lines.append(
            f"• {provider.get('label')}: 실제 {_won(provider.get('actual_hourly_max'))} "
            f"· 주휴* {weekly} · 원문 {provider.get('source_count')}개 · "
            f"{_source_link(provider.get('representative_url'))}"
        )
    if hotel.get("cross_agency_duplicate_count"):
        lines.append(
            f"↳ 동일 조건 교차업체 비교 {hotel['cross_agency_duplicate_count']}건"
        )
    if hotel.get("source_snapshot_stale"):
        lines.append("⚠ 일부 원문은 최근 8시간 이내 소스 스냅샷 기준")
    return "\n".join(lines)


def _paginate_blocks(
    *,
    title: str,
    intro: str,
    blocks: list[tuple[str, str]],
    separator: str = "\n\n",
) -> list[DigestPage]:
    # Reserve enough room for a multi-digit page label before chunking.
    prefix_reserve = len(title) + len(intro) + 32
    block_limit = DISCORD_MESSAGE_LIMIT - prefix_reserve
    expanded_blocks: list[tuple[str, str]] = []
    for hotel_id, block in blocks:
        if len(block) <= block_limit:
            expanded_blocks.append((hotel_id, block))
            continue
        lines = block.splitlines()
        continuation = f"{lines[0]} _(계속)_" if lines else "_(계속)_"
        fragment: list[str] = []
        fragment_length = 0
        for line in lines:
            pieces = [
                line[offset : offset + block_limit]
                for offset in range(0, max(1, len(line)), block_limit)
            ] or [""]
            for piece in pieces:
                addition = len(piece) + (1 if fragment else 0)
                if fragment and fragment_length + addition > block_limit:
                    expanded_blocks.append((hotel_id, "\n".join(fragment)))
                    fragment = [continuation]
                    fragment_length = len(continuation)
                fragment.append(piece)
                fragment_length += len(piece) + (1 if len(fragment) > 1 else 0)
        if fragment:
            expanded_blocks.append((hotel_id, "\n".join(fragment)))

    chunks: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_length = prefix_reserve
    for hotel_id, block in expanded_blocks:
        addition = len(block) + (len(separator) if current else 0)
        if current and current_length + addition > DISCORD_MESSAGE_LIMIT:
            chunks.append(current)
            current = []
            current_length = prefix_reserve
        current.append((hotel_id, block))
        current_length += len(block) + (len(separator) if len(current) > 1 else 0)
    if current or not chunks:
        chunks.append(current)

    pages: list[DigestPage] = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        header = f"{title} [{index}/{total}]\n{intro}".rstrip()
        body = separator.join(block for _, block in chunk)
        message = f"{header}\n\n{body}" if body else header
        if len(message) > DISCORD_MESSAGE_LIMIT:
            raise ValueError("Discord pagination produced an oversized page")
        pages.append(
            DigestPage(
                message=message,
                hotel_ids=tuple(hotel_id for hotel_id, _ in chunk),
            )
        )
    return pages


def _overview_pages(
    hotels: list[dict[str, Any]], report: dict[str, Any]
) -> list[DigestPage]:
    rows = [
        (str(hotel["hotel_id"]), _overview_row(hotel)) for hotel in hotels
    ]
    all_live = bool(report.get("all_sources_live"))
    intro = (
        f"{len(hotels)}개 호텔 · {len(report.get('jobs') or [])}개 선택지 · "
        f"{sum(int(hotel.get('offer_count') or 0) for hotel in hotels)}개 업체 공고행\n"
        "정렬: 실제/주휴 제외 최고시급↓ → 통근↑\n"
        "주간 = 06:00–20:00 안에 완결 · T = 서로 다른 정확한 시간대\n"
        "`# | 호텔 | 실제최대/주간최대 | 주휴*최대 | 통근 | 날짜/T/선택지 | 최고업체`\n"
        f"소스: {'모두 실시간' if all_live else '최근 스냅샷 포함 ⚠'}"
    )
    return _paginate_blocks(
        title="🏨 **주말 호텔알바 전체 요약**",
        intro=intro,
        blocks=rows,
        separator="\n",
    )


def format_complete_digest(
    report: dict[str, Any], hotels: list[dict[str, Any]]
) -> list[DigestPage]:
    overview = _overview_pages(hotels, report)
    details = _paginate_blocks(
        title="🏨 **호텔별 날짜·시간·업체 상세**",
        intro=(
            "실제 = 주휴 제외 비교시급 · 주휴* = 조건 충족 시 참고값\n"
            "주간 = 06:00–20:00 안에 완결되는 근무 · 각 시간의 금액은 "
            "그 날짜·시간 조합에서 확인된 최고 실제시급입니다."
        ),
        blocks=[
            (str(hotel["hotel_id"]), _hotel_block(hotel)) for hotel in hotels
        ],
    )
    return [*overview, *details]


def format_update_digest(
    changed_hotels: list[tuple[str, dict[str, Any]]]
) -> list[DigestPage]:
    return _paginate_blocks(
        title="🏨 **호텔 단기알바 변경사항**",
        intro="같은 호텔의 날짜·시간·업체를 다시 묶은 최신 전체 상태입니다.",
        blocks=[
            (
                str(hotel["hotel_id"]),
                _hotel_block(hotel, change_kind=change_kind),
            )
            for change_kind, hotel in changed_hotels
        ],
    )
