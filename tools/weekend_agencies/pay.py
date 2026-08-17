"""Source-aware pay parsing with explicit weekly-allowance separation."""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

from .models import PayBreakdown


WON_RE = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{4,6})(?!\d)")
TIME_RE = re.compile(
    r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)\s*[~\-]\s*"
    r"([01]?\d|2[0-3]):([0-5]\d)(?!\d)"
)


def amounts(text: str) -> list[int]:
    return [int(match.replace(",", "")) for match in WON_RE.findall(text)]


def base_from_weekly_allowance(value: int) -> int:
    return int((Decimal(value) / Decimal("1.2")).quantize(Decimal("1"), ROUND_HALF_UP))


def weekly_allowance_from_base(value: int) -> int:
    return int((Decimal(value) * Decimal("1.2")).quantize(Decimal("1"), ROUND_HALF_UP))


def shift_duration_hours(start: str, end: str) -> Decimal | None:
    try:
        start_hour, start_minute = (int(part) for part in start.split(":"))
        end_hour, end_minute = (int(part) for part in end.split(":"))
    except (TypeError, ValueError):
        return None
    start_total = start_hour * 60 + start_minute
    end_total = end_hour * 60 + end_minute
    if end_total <= start_total:
        end_total += 24 * 60
    duration = end_total - start_total
    return Decimal(duration) / Decimal(60) if duration else None


def parse_withyou_pay(text: str) -> PayBreakdown:
    values = amounts(text)
    if not values:
        return PayBreakdown(None, None, text, "unparseable")
    if "주휴포함" in text and len(values) == 1:
        weekly = values[0]
        return PayBreakdown(
            base_from_weekly_allowance(weekly),
            weekly,
            text,
            "derived_from_weekly_allowance",
            "advertised_weekly_allowance_inclusive",
            warnings=("advertised amount includes 주휴; base pay is derived",),
        )
    return PayBreakdown(
        values[0],
        values[1] if len(values) > 1 else None,
        text,
        "advertised_hourly",
        "advertised_separately" if len(values) > 1 else "not_stated",
    )


def parse_huplux_pay(text: str, start: str, end: str) -> PayBreakdown:
    values = amounts(text)
    if not values:
        return PayBreakdown(None, None, text, "unparseable")

    weekend_match = re.search(
        r"주말\s*(\d{1,3}(?:,\d{3})+|\d{4,6})", text
    )
    if weekend_match:
        actual = int(weekend_match.group(1).replace(",", ""))
        return PayBreakdown(actual, None, text, "advertised_weekend_hourly")

    if "주휴발생시" in text or "주휴포함" in text:
        weekly = values[0]
        return PayBreakdown(
            base_from_weekly_allowance(weekly),
            weekly,
            text,
            "derived_from_weekly_allowance",
            "advertised_weekly_allowance_inclusive",
            warnings=("advertised amount applies only when 주휴 is earned",),
        )

    if "일급" in text:
        duration = shift_duration_hours(start, end)
        if duration is None:
            return PayBreakdown(
                None,
                None,
                text,
                "daily_pay_without_reliable_shift_duration",
                pay_frequency="daily",
            )
        actual = int((Decimal(values[0]) / duration).quantize(Decimal("1"), ROUND_HALF_UP))
        return PayBreakdown(
            actual,
            None,
            text,
            "derived_from_daily_pay_and_scheduled_hours",
            pay_frequency="daily",
            warnings=("break time is not stated; hourly equivalent uses scheduled hours",),
        )

    return PayBreakdown(values[0], None, text, "advertised_hourly")


def _headline_weekly_allowance(title: str, actual: int) -> tuple[int | None, str, tuple[str, ...]]:
    title_values = amounts(title)
    if not title_values:
        return None, "not_stated", ()
    expected = weekly_allowance_from_base(actual)
    explicit = next(
        (
            value
            for value in title_values
            if abs(value - expected) <= max(20, int(expected * 0.01))
        ),
        None,
    )
    if explicit is not None:
        return explicit, "headline_matches_base_x_1.2", ()
    if "주휴" in title:
        return (
            None,
            "conflicting_headline_not_used",
            ("headline 주휴 amount conflicts with the listed pay and was not used",),
        )
    return None, "not_stated", ()


def parse_eumplus_pay(text: str, title: str) -> PayBreakdown:
    values = amounts(text)
    if not values:
        return PayBreakdown(None, None, text, "unparseable", pay_frequency="weekly")
    actual = values[-1]
    weekly, weekly_basis, warnings = _headline_weekly_allowance(title, actual)
    return PayBreakdown(
        actual,
        weekly,
        text,
        "listed_hourly_rate",
        weekly_basis,
        pay_frequency="weekly",
        warnings=warnings,
    )


def parse_htservice_pay(headline_text: str, detail_text: str) -> PayBreakdown:
    """Use HTService's detail-disclosed base, never its 주휴 headline as base."""

    headline_values = amounts(headline_text)
    headline = headline_values[0] if headline_values else None
    pairs = {
        int(weekly.replace(",", "")): int(actual.replace(",", ""))
        for weekly, actual in re.findall(
            r"주휴시급\s*(\d{1,3}(?:,\d{3})+|\d{4,6})원?\s*"
            r"받는분\s*기본시급\s*(\d{1,3}(?:,\d{3})+|\d{4,6})원?",
            detail_text,
        )
    }
    actual = pairs.get(headline) if headline is not None else None
    if actual is None:
        return PayBreakdown(
            None,
            headline,
            headline_text,
            "detail_did_not_verify_base",
            "advertised_weekly_allowance_inclusive" if headline else "not_stated",
            warnings=(
                "HTService headline may include 주휴; detail did not verify its base rate",
            ),
        )
    return PayBreakdown(
        actual,
        headline,
        headline_text,
        "detail_verified_base_rate",
        "advertised_weekly_allowance_inclusive",
        warnings=("advertised headline is 주휴-inclusive; base comes from detail",),
    )


def parse_oulim_pay(text: str, title: str, start: str, end: str) -> PayBreakdown:
    """Parse Oulim's mixed hourly/daily list cell conservatively."""

    values = amounts(text)
    if not values:
        return PayBreakdown(None, None, text, "unparseable")
    value = values[0]
    if value >= 50_000:
        return parse_huplux_pay(f"일급 {value}", start, end)
    if "주휴포함" in title.replace(" ", ""):
        return PayBreakdown(
            base_from_weekly_allowance(value),
            value,
            text,
            "derived_from_weekly_allowance",
            "advertised_weekly_allowance_inclusive",
            warnings=("advertised amount includes 주휴; base pay is derived",),
        )
    if "주휴수당별도" in title.replace(" ", ""):
        return PayBreakdown(
            value,
            weekly_allowance_from_base(value),
            text,
            "listed_hourly_rate",
            "derived_base_x_1.2_if_eligible",
            warnings=("주휴 is stated separately; eligibility is not guaranteed",),
        )
    return PayBreakdown(value, None, text, "listed_hourly_rate")


def parse_rutine_pay(text: str, start: str, end: str) -> PayBreakdown:
    """Parse Rutine's hourly/daily cells without treating 주휴 as base pay."""

    compact_text = text.replace(" ", "")
    values = amounts(text)
    if not values:
        return PayBreakdown(None, None, text, "unparseable")
    value = values[0]
    if "일급" in compact_text:
        return parse_huplux_pay(f"일급 {value}", start, end)
    if "주휴포함" in compact_text:
        return PayBreakdown(
            base_from_weekly_allowance(value),
            value,
            text,
            "derived_from_weekly_allowance",
            "advertised_weekly_allowance_inclusive",
            warnings=("advertised amount includes 주휴; base pay is derived",),
        )
    if "주휴별도" in compact_text or "주휴수당별도" in compact_text:
        return PayBreakdown(
            value,
            weekly_allowance_from_base(value),
            text,
            "listed_hourly_rate",
            "derived_base_x_1.2_if_eligible",
            warnings=("주휴 is stated separately; eligibility is not guaranteed",),
        )
    return PayBreakdown(value, None, text, "listed_hourly_rate")
