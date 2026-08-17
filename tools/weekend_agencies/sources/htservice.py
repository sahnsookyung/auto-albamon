"""Detail-verified parser for HTService's public daily job board."""

from __future__ import annotations

import html
import re
import urllib.parse
from dataclasses import dataclass
from datetime import date

from ..config import SEARCH_DATES
from ..html_table import clean_text, row_texts, table_rows
from ..http import HttpClient
from ..models import JobOffer
from ..normalize import extract_shifts, role_category
from ..pay import parse_htservice_pay


BASE_URL = "http://htservice.kr"
LIST_URL = f"{BASE_URL}/daily_info.html"
ID_RE = re.compile(r"[?&]idx=(\d+)")
TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class HTServiceListing:
    identifier: str
    slot_index: int
    work_date: date
    employer: str
    title: str
    shift_start: str
    shift_end: str
    station: str
    requirements: str
    headline_pay: str
    status: str

    @property
    def detail_url(self) -> str:
        query = urllib.parse.urlencode(
            {"svc_name": "dayjob", "ptype": "view", "idx": self.identifier}
        )
        return f"{LIST_URL}?{query}"


def document_text(document: str) -> str:
    return clean_text(TAG_RE.sub(" ", html.unescape(document)))


def parse_list_page(document: str) -> list[HTServiceListing]:
    listings: list[HTServiceListing] = []
    for row in table_rows(document):
        cells = row_texts(row)
        if len(cells) < 8:
            continue
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", cells[0])
        identifier = ID_RE.search(row.attributes.get("onclick", ""))
        shifts = extract_shifts(cells[3])
        if not date_match or not identifier or not shifts:
            continue
        for slot_index, (start, end) in enumerate(shifts):
            listings.append(
                HTServiceListing(
                    identifier=identifier.group(1),
                    slot_index=slot_index,
                    work_date=date.fromisoformat(date_match.group(0)),
                    employer=cells[1],
                    title=cells[2],
                    shift_start=start,
                    shift_end=end,
                    station=cells[4],
                    requirements=" / ".join(part for part in (cells[2], cells[5]) if part),
                    headline_pay=cells[6],
                    status="open" if "모집중" in cells[7] else "closed",
                )
            )
    return listings


def offer_from_listing(listing: HTServiceListing, detail_document: str) -> JobOffer:
    return JobOffer(
        agency="htservice",
        source_id=f"{listing.identifier}:{listing.slot_index}",
        source_url=listing.detail_url,
        work_date=listing.work_date,
        employer=listing.employer,
        title=listing.title,
        role_category=role_category(listing.title),
        shift_start=listing.shift_start,
        shift_end=listing.shift_end,
        pay=parse_htservice_pay(listing.headline_pay, document_text(detail_document)),
        station=listing.station,
        requirements=listing.requirements,
        status=listing.status,
        metadata={
            "detail_pay_verified": True,
            "transport_security": "unencrypted_http",
        },
    )


class HTServiceSource:
    name = "htservice"

    def fetch(
        self,
        client: HttpClient,
        *,
        dates: tuple[date, ...] = SEARCH_DATES,
    ) -> list[JobOffer]:
        target_dates = set(dates)
        listings: list[HTServiceListing] = []
        seen_ids: set[str] = set()
        for page in range(1, 11):
            query = urllib.parse.urlencode({"svc_name": "dayjob", "page": page})
            page_listings = parse_list_page(client.get(f"{LIST_URL}?{query}"))
            fresh = [
                item
                for item in page_listings
                if f"{item.identifier}:{item.slot_index}" not in seen_ids
            ]
            if not fresh:
                break
            seen_ids.update(
                f"{item.identifier}:{item.slot_index}" for item in fresh
            )
            listings.extend(item for item in fresh if item.work_date in target_dates)
        return [
            offer_from_listing(item, client.get(item.detail_url)) for item in listings
        ]
