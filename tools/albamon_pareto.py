"""Pareto ranking helpers for comparable Albamon job choices."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


def _candidate(record: dict[str, Any]) -> tuple[str, int, int, int] | None:
    """Return ``(mode, commute, pay, recruit_no)`` for a comparable job."""

    if record.get("application_disposition") != "apply":
        return None
    try:
        recruit_no = int(record["recruit_no"])
        pay = int(record["comparable_hourly_pay"])
    except (KeyError, TypeError, ValueError):
        return None
    if recruit_no <= 0 or pay <= 0:
        return None

    lanes = set(record.get("eligibility_lanes") or [])
    remote = bool(record.get("remote_job") or "remote_short_term" in lanes)
    if remote:
        return ("remote", 0, pay, recruit_no)
    if record.get("commute_policy_eligible") is not True:
        return None
    try:
        commute = int(record["transit_minutes"])
    except (KeyError, TypeError, ValueError):
        return None
    if commute < 0:
        return None
    return ("onsite", commute, pay, recruit_no)


def pareto_frontier_ids(records: Iterable[dict[str, Any]]) -> set[int]:
    """Find actionable pay/commute trade-offs in O(n log n) time.

    A job is dominated when another job in the same commute mode pays at least
    as much, takes no longer to reach, and is strictly better on either axis.
    Remote and on-site work are intentionally separate modes: otherwise one
    remote role with zero commute could hide every useful on-site option.
    """

    grouped: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    for record in records:
        candidate = _candidate(record)
        if candidate is None:
            continue
        mode, commute, pay, recruit_no = candidate
        grouped[mode].append((commute, pay, recruit_no))

    frontier: set[int] = set()
    for candidates in grouped.values():
        candidates.sort(key=lambda item: (item[0], -item[1], item[2]))
        best_pay_at_shorter_commute = -1
        index = 0
        while index < len(candidates):
            commute = candidates[index][0]
            end = index + 1
            while end < len(candidates) and candidates[end][0] == commute:
                end += 1
            group = candidates[index:end]
            group_best_pay = group[0][1]
            if group_best_pay > best_pay_at_shorter_commute:
                frontier.update(
                    recruit_no
                    for _minutes, pay, recruit_no in group
                    if pay == group_best_pay
                )
            best_pay_at_shorter_commute = max(
                best_pay_at_shorter_commute, group_best_pay
            )
            index = end
    return frontier


def annotate_pareto_frontier(records: list[dict[str, Any]]) -> int:
    """Add a stable boolean frontier marker to every record and return its size."""

    frontier = pareto_frontier_ids(records)
    for record in records:
        try:
            recruit_no = int(record.get("recruit_no"))
        except (TypeError, ValueError):
            recruit_no = 0
        record["pareto_frontier"] = recruit_no in frontier
    return len(frontier)
