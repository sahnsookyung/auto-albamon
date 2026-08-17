"""Parser for Rutine's anonymous public dated hotel-shift table."""

from __future__ import annotations

import hashlib
import re
from datetime import date

from ..config import SEARCH_DATES
from ..html_table import row_texts, table_rows
from ..http import HttpClient
from ..models import JobOffer
from ..normalize import extract_shifts, role_category
from ..pay import parse_rutine_pay


LIST_URL = "https://www.xn--2s2bi09b.com/"
PUBLIC_TABLE_URL = f"{LIST_URL}#s2022010739d52232b60ce"
DATE_RE = re.compile(r"(?<!\d)(\d{2})/(\d{2})(?!\d)")


def parse_page(
    document: str, *, dates: tuple[date, ...] = SEARCH_DATES
) -> list[JobOffer]:
    dates_by_month_day = {(value.month, value.day): value for value in dates}
    offers: list[JobOffer] = []
    for row in table_rows(document):
        cells = row_texts(row)
        if len(cells) < 6:
            continue
        date_match = DATE_RE.fullmatch(cells[0])
        shifts = extract_shifts(cells[3])
        if not date_match or not shifts:
            continue
        work_date = dates_by_month_day.get(
            (int(date_match.group(1)), int(date_match.group(2)))
        )
        if work_date is None:
            continue
        employer = cells[1].strip()
        title = cells[2].strip()
        status = "open" if "모집중" in cells[5] else "closed"
        for slot_index, (start, end) in enumerate(shifts):
            identity = "|".join(
                (
                    work_date.isoformat(),
                    employer,
                    title,
                    start,
                    end,
                    cells[4],
                    str(slot_index),
                )
            )
            source_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
            offers.append(
                JobOffer(
                    agency="rutine",
                    source_id=source_id,
                    source_url=PUBLIC_TABLE_URL,
                    work_date=work_date,
                    employer=employer,
                    title=title,
                    role_category=role_category(title),
                    shift_start=start,
                    shift_end=end,
                    pay=parse_rutine_pay(cells[4], start, end),
                    requirements=title,
                    status=status,
                )
            )
    return offers


def listing_metrics(document: str) -> dict[str, int]:
    rows = 0
    open_rows = 0
    for row in table_rows(document):
        cells = row_texts(row)
        if len(cells) < 6 or not DATE_RE.fullmatch(cells[0]):
            continue
        rows += 1
        open_rows += "모집중" in cells[5]
    return {"raw_listing_rows": rows, "unique_open_listing_ids": open_rows}


class RutineSource:
    name = "rutine"

    def __init__(self) -> None:
        self.last_fetch_metrics: dict[str, int] = {}

    def fetch(
        self,
        client: HttpClient,
        *,
        dates: tuple[date, ...] = SEARCH_DATES,
    ) -> list[JobOffer]:
        document = client.get(LIST_URL)
        offers = parse_page(document, dates=dates)
        metrics = listing_metrics(document)
        self.last_fetch_metrics = {
            "pages_fetched": 1,
            **metrics,
            "unique_listing_ids": metrics["raw_listing_rows"],
            "target_date_offer_count": len(offers),
        }
        return offers
