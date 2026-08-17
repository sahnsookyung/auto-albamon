"""Bounded parser for Oulim's anonymous public hotel-shift list."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import replace
from datetime import date

from ..config import SEARCH_DATES, SEARCH_START
from ..html_table import first_link, row_texts, table_rows
from ..http import HttpClient
from ..models import JobOffer
from ..normalize import extract_shifts, role_category
from ..pay import parse_oulim_pay


BASE_URL = "https://www.oulim.kr"
LIST_URL = f"{BASE_URL}/alba/work_total_list.asp"
ID_RE = re.compile(r"[?&]num=(\d+)")
DATE_RE = re.compile(r"(?<!\d)(\d{2})\.(\d{2})(?!\d)")


def page_dates(document: str, *, year: int = SEARCH_START.year) -> list[date]:
    result: list[date] = []
    for row in table_rows(document):
        cells = row_texts(row)
        if not cells:
            continue
        match = DATE_RE.fullmatch(cells[0])
        if not match:
            continue
        try:
            result.append(date(year, int(match.group(1)), int(match.group(2))))
        except ValueError:
            continue
    return result


def parse_page(
    document: str, *, dates: tuple[date, ...] = SEARCH_DATES
) -> list[JobOffer]:
    dates_by_month_day = {(value.month, value.day): value for value in dates}
    offers: list[JobOffer] = []
    for row in table_rows(document):
        cells = row_texts(row)
        if len(cells) < 9:
            continue
        date_match = DATE_RE.fullmatch(cells[0])
        href = first_link(row)
        identifier = ID_RE.search(href)
        shifts = extract_shifts(cells[4])
        if not date_match or not identifier or not shifts:
            continue
        work_date = dates_by_month_day.get(
            (int(date_match.group(1)), int(date_match.group(2)))
        )
        if work_date is None:
            continue
        status_images = {
            image.rsplit("/", 1)[-1].lower()
            for cell in row.cells
            for image in cell.images
        }
        status = "open" if "ing.gif" in status_images else "closed"
        employer = cells[2].rstrip(". ")
        title = cells[3]
        for slot_index, (start, end) in enumerate(shifts):
            pay = parse_oulim_pay(cells[7], title, start, end)
            if "종료시" in cells[4]:
                pay = replace(
                    pay,
                    warnings=pay.warnings
                    + ("listed end time may extend until work is complete",),
                )
            offers.append(
                JobOffer(
                    agency="eoullim",
                    source_id=f"{identifier.group(1)}:{slot_index}",
                    source_url=urllib.parse.urljoin(BASE_URL, href),
                    work_date=work_date,
                    employer=employer,
                    title=title,
                    role_category=role_category(title),
                    shift_start=start,
                    shift_end=end,
                    pay=pay,
                    requirements=" / ".join(
                        part for part in (title, cells[5], cells[6]) if part
                    ),
                    status=status,
                    metadata={
                        "detail_requires_login": True,
                        "advertised_shift_text": cells[4],
                        "end_time_may_extend": "종료시" in cells[4],
                    },
                )
            )
    return offers


def last_page_number(document: str) -> int:
    pages = [int(value) for value in re.findall(r"[?&]pageno=(\d+)", document)]
    return min(max(pages, default=1), 25)


def listing_markers(document: str) -> list[tuple[str, bool]]:
    """Return one marker per rendered list row for source observability."""

    markers: list[tuple[str, bool]] = []
    for row in table_rows(document):
        cells = row_texts(row)
        if len(cells) < 9 or not DATE_RE.fullmatch(cells[0]):
            continue
        identifier = ID_RE.search(first_link(row))
        if not identifier:
            continue
        images = {
            image.rsplit("/", 1)[-1].lower()
            for cell in row.cells
            for image in cell.images
        }
        markers.append((identifier.group(1), "ing.gif" in images))
    return markers


class EoullimSource:
    name = "eoullim"

    def __init__(self) -> None:
        self.last_fetch_metrics: dict[str, int] = {}

    def fetch(
        self,
        client: HttpClient,
        *,
        dates: tuple[date, ...] = SEARCH_DATES,
    ) -> list[JobOffer]:
        target_dates = set(dates)
        all_offers: list[JobOffer] = []
        seen_ids: set[str] = set()
        raw_markers: list[tuple[str, bool]] = []
        first_query = urllib.parse.urlencode({"pageno": 1})
        first_document = client.get(
            f"{LIST_URL}?{first_query}", encoding="cp949"
        )
        page_count = last_page_number(first_document)
        for page in range(1, page_count + 1):
            if page == 1:
                document = first_document
            else:
                query = urllib.parse.urlencode({"pageno": page})
                document = client.get(
                    f"{LIST_URL}?{query}", encoding="cp949"
                )
            raw_markers.extend(listing_markers(document))
            page_offers = [
                item
                for item in parse_page(document, dates=dates)
                if item.work_date in target_dates
            ]
            fresh = [item for item in page_offers if item.source_id not in seen_ids]
            all_offers.extend(fresh)
            seen_ids.update(item.source_id for item in fresh)
        unique_marker_states: dict[str, bool] = {}
        for identifier, is_open in raw_markers:
            unique_marker_states[identifier] = (
                unique_marker_states.get(identifier, False) or is_open
            )
        self.last_fetch_metrics = {
            "pages_fetched": page_count,
            "raw_listing_rows": len(raw_markers),
            "duplicate_listing_rows": len(raw_markers) - len(unique_marker_states),
            "unique_listing_ids": len(unique_marker_states),
            "unique_open_listing_ids": sum(unique_marker_states.values()),
            "target_date_offer_count": len(all_offers),
        }
        return all_offers
