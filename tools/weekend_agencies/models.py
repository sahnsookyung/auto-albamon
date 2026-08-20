"""Typed domain records shared by source adapters and reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class PayBreakdown:
    """A comparable wage that never disguises weekly allowance as base pay."""

    actual_hourly_won: int | None
    weekly_allowance_hourly_won: int | None
    advertised_text: str
    actual_basis: str
    weekly_allowance_basis: str = "not_stated"
    pay_frequency: str = "hourly"
    advertised_shift_won: int | None = None
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class JobOffer:
    agency: str
    source_id: str
    source_url: str
    work_date: date
    employer: str
    title: str
    role_category: str
    shift_start: str
    shift_end: str
    pay: PayBreakdown
    area: str = ""
    station: str = ""
    requirements: str = ""
    status: str = "open"
    latitude: float | None = None
    longitude: float | None = None
    location_basis: str = "unresolved"
    transit_minutes: int | None = None
    transit_status: str = "unverified"
    transit_basis: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def shift(self) -> str:
        return f"{self.shift_start}~{self.shift_end}"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["work_date"] = self.work_date.isoformat()
        payload["shift"] = self.shift
        return payload
