"""Parser and bounded paginator for Huplux's public one-day list."""

from __future__ import annotations

import re
import urllib.parse
from datetime import date

from ..config import SEARCH_DATES
from ..html_table import first_link, row_texts, table_rows
from ..http import HttpClient
from ..models import JobOffer
from ..normalize import extract_shifts, role_category
from ..pay import parse_huplux_pay


BASE_URL = "https://www.huplux.com"
LIST_URL = f"{BASE_URL}/product/list.php"
ID_RE = re.compile(r"[?&]it_id=(\d+)")


def parse_page(document: str) -> list[JobOffer]:
    offers: list[JobOffer] = []
    for row in table_rows(document):
        cells = row_texts(row)
        if len(cells) < 10:
            continue
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", cells[1])
        href = first_link(row)
        identifier = ID_RE.search(href)
        shifts = extract_shifts(cells[7])
        if not date_match or not identifier or not shifts:
            continue
        status = "open" if "구인" in cells[9] or "급구" in cells[9] else "closed"
        employer = re.sub(r"^\s*\[서울\]\s*", "", cells[2]).strip()
        title = " / ".join(part for part in (cells[3], cells[4]) if part)
        for slot_index, (start, end) in enumerate(shifts):
            offers.append(
                JobOffer(
                    agency="huplux",
                    source_id=f"{identifier.group(1)}:{slot_index}",
                    source_url=urllib.parse.urljoin(BASE_URL, href),
                    work_date=date.fromisoformat(date_match.group(0)),
                    employer=employer,
                    title=title,
                    role_category=role_category(title),
                    shift_start=start,
                    shift_end=end,
                    pay=parse_huplux_pay(cells[6], start, end),
                    area=cells[0],
                    requirements=" / ".join(part for part in (cells[4], cells[5]) if part),
                    status=status,
                )
            )
    return offers


class HupluxSource:
    name = "huplux"

    def fetch(
        self,
        client: HttpClient,
        *,
        dates: tuple[date, ...] = SEARCH_DATES,
    ) -> list[JobOffer]:
        all_offers: list[JobOffer] = []
        seen_ids: set[str] = set()
        for work_date in dates:
            for page in range(1, 11):
                query = urllib.parse.urlencode(
                    {
                        "it_type": 2,
                        "Seachkeyword": "",
                        "ca_name": "서울시",
                        "start_time": work_date.isoformat(),
                        "it_ex37": "",
                        "it_ex38": "",
                        "page": page,
                    }
                )
                page_offers = parse_page(client.get(f"{LIST_URL}?{query}"))
                fresh = [item for item in page_offers if item.source_id not in seen_ids]
                if not fresh:
                    break
                all_offers.extend(fresh)
                seen_ids.update(item.source_id for item in fresh)
                listing_count = len(
                    {item.source_id.split(":", 1)[0] for item in page_offers}
                )
                if listing_count < 15:
                    break
        return all_offers
