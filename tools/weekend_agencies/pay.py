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
