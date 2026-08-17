"""Hotel-level Pareto frontiers for pay and verified commute.

The agency report preserves actual/base pay separately from the conditional
weekly-allowance headline.  The dashboard therefore exposes two independent
frontiers instead of mutating the source ranking:

* actual: reliable actual/base hourly pay;
* weekly-aware: the published weekly-allowance rate when present, otherwise
  the actual/base rate (with the fallback called out in the UI).
"""

from __future__ import annotations

from typing import Any, Iterable


def comparable_pay(hotel: dict[str, Any], mode: str) -> int | None:
    """Return the hotel pay used by one dashboard comparison mode."""

    actual = hotel.get("actual_hourly_max")
    if not isinstance(actual, int) or actual <= 0:
        return None
    if mode == "actual":
        return actual
    if mode != "weekly_allowance":
        raise ValueError(f"Unsupported hotel Pareto mode: {mode}")
    weekly = hotel.get("weekly_allowance_hourly_max")
    return weekly if isinstance(weekly, int) and weekly > 0 else actual


def pareto_hotel_ids(
    hotels: Iterable[dict[str, Any]], *, mode: str
) -> set[str]:
    """Return hotels not dominated on comparable pay and verified commute."""

    candidates: list[tuple[str, int, int]] = []
    for hotel in hotels:
        hotel_id = str(hotel.get("hotel_id") or "")
        pay = comparable_pay(hotel, mode)
        commute = hotel.get("transit_minutes")
        if hotel_id and pay is not None and isinstance(commute, int) and commute >= 0:
            candidates.append((hotel_id, pay, commute))

    frontier: set[str] = set()
    for hotel_id, pay, commute in candidates:
        dominated = any(
            other_pay >= pay
            and other_commute <= commute
            and (other_pay > pay or other_commute < commute)
            for other_id, other_pay, other_commute in candidates
            if other_id != hotel_id
        )
        if not dominated:
            frontier.add(hotel_id)
    return frontier


def annotate_hotel_frontiers(hotels: list[dict[str, Any]]) -> dict[str, int]:
    """Add both frontier markers and comparable pay values to hotel rows."""

    actual_ids = pareto_hotel_ids(hotels, mode="actual")
    weekly_ids = pareto_hotel_ids(hotels, mode="weekly_allowance")
    for hotel in hotels:
        hotel_id = str(hotel.get("hotel_id") or "")
        weekly = hotel.get("weekly_allowance_hourly_max")
        hotel["pareto_actual"] = hotel_id in actual_ids
        hotel["pareto_weekly_allowance"] = hotel_id in weekly_ids
        hotel["pareto_actual_pay"] = comparable_pay(hotel, "actual")
        hotel["pareto_weekly_allowance_pay"] = comparable_pay(
            hotel, "weekly_allowance"
        )
        hotel["weekly_allowance_rate_stated"] = isinstance(weekly, int) and weekly > 0
    return {
        "actual": len(actual_ids),
        "weekly_allowance": len(weekly_ids),
    }
