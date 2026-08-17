"""Parser and bounded paginator for WithYou's public day-work list."""

from __future__ import annotations

import re
import urllib.parse
from datetime import date

from ..config import SEARCH_END, SEARCH_START
from ..html_table import first_link, row_texts, table_rows
from ..http import HttpClient
from ..models import JobOffer
from ..normalize import extract_shifts, role_category
from ..pay import parse_withyou_pay


BASE_URL = "https://with-you.kr"
LIST_URL = f"{BASE_URL}/day.php"
ID_RE = re.compile(r"[?&]idx=(\d+)")


def parse_page(document: str) -> list[JobOffer]:
    offers: list[JobOffer] = []
    for row in table_rows(document):
        cells = row_texts(row)
        if len(cells) < 7 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]):
            continue
        href = first_link(row)
        identifier = ID_RE.search(href)
        shifts = extract_shifts(cells[2])
        if not identifier or not shifts:
            continue
        status = "open" if "모집중" in cells[6] else "closed"
        for slot_index, (start, end) in enumerate(shifts):
            offers.append(
                JobOffer(
                    agency="withyou",
                    source_id=f"{identifier.group(1)}:{slot_index}",
                    source_url=urllib.parse.urljoin(BASE_URL, href),
                    work_date=date.fromisoformat(cells[0]),
                    employer=cells[1],
                    title=cells[2],
                    role_category=role_category(cells[2]),
                    shift_start=start,
                    shift_end=end,
                    pay=parse_withyou_pay(cells[3]),
                    requirements=cells[2],
                    status=status,
                )
            )
    return offers


class WithYouSource:
    name = "withyou"

    def fetch(
        self,
        client: HttpClient,
        *,
        start: date = SEARCH_START,
        end: date = SEARCH_END,
    ) -> list[JobOffer]:
        all_offers: list[JobOffer] = []
        seen_ids: set[str] = set()
        for page in range(1, 11):
            query = urllib.parse.urlencode(
                {
                    "frdate": start.isoformat(),
                    "todate": end.isoformat(),
                    "fs_area": "",
                    "fs_hotel": "",
                    "page": page,
                }
            )
            page_offers = parse_page(client.get(f"{LIST_URL}?{query}"))
            fresh = [item for item in page_offers if item.source_id not in seen_ids]
            if not fresh:
                break
            all_offers.extend(fresh)
            seen_ids.update(item.source_id for item in fresh)
            if len({item.source_id.split(":", 1)[0] for item in page_offers}) < 100:
                break
        return all_offers
