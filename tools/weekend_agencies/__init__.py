"""Weekend-only staffing-agency job discovery, separate from Albamon."""

from .agencies import (
    HOTEL_AGENCIES,
    HotelAgency,
    hotel_agency_catalog_payload,
    hotel_agency_source_coverage_payload,
    resolve_hotel_agency,
)
from .config import SEARCH_DATES, SEARCH_END, SEARCH_START
from .models import JobOffer, PayBreakdown

__all__ = [
    "HOTEL_AGENCIES",
    "HotelAgency",
    "JobOffer",
    "PayBreakdown",
    "SEARCH_DATES",
    "SEARCH_END",
    "SEARCH_START",
    "hotel_agency_catalog_payload",
    "hotel_agency_source_coverage_payload",
    "resolve_hotel_agency",
]
