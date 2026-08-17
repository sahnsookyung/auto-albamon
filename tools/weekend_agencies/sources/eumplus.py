"""Parser for Eumplus's public daily list (details require membership)."""

from __future__ import annotations

import re
import urllib.parse
from datetime import date

from ..config import SEARCH_DATES
from ..html_table import row_texts, table_rows
from ..http import HttpClient
from ..models import JobOffer
from ..normalize import extract_shifts, role_category
from ..pay import parse_eumplus_pay


BASE_URL = "http://www.eumplus.co.kr"
LIST_URL = f"{BASE_URL}/_alba/alba.php"
SEQ_RE = re.compile(r"Seq=(\d+)")


def parse_page(document: str, requested_date: date) -> list[JobOffer]:
    offers: list[JobOffer] = []
    for row in table_rows(document):
        cells = row_texts(row)
        if len(cells) < 8 or not re.fullmatch(r"\d{2}월\d{2}일\s*[토일]", cells[0]):
            continue
        onclick = row.attributes.get("onclick", "")
        identifier = SEQ_RE.search(onclick)
        shifts = extract_shifts(cells[3])
        if not identifier or not shifts:
            continue
        status = "open" if "모집중" in cells[7] else "closed"
        detail_path = re.search(r"location\.href=['\"]([^'\"]+)", onclick)
        detail_url = urllib.parse.urljoin(
            f"{BASE_URL}/_alba/",
            detail_path.group(1) if detail_path else "alba.php",
        )
        for slot_index, (start, end) in enumerate(shifts):
            offers.append(
                JobOffer(
                    agency="eumplus",
                    source_id=f"{identifier.group(1)}:{slot_index}",
                    source_url=detail_url,
                    work_date=requested_date,
                    employer=cells[1],
                    title=cells[2],
                    role_category=role_category(cells[2]),
                    shift_start=start,
                    shift_end=end,
                    pay=parse_eumplus_pay(cells[6], cells[2]),
                    station=cells[4],
                    requirements=cells[2],
                    status=status,
                    metadata={"detail_requires_login": True},
                )
            )
    return offers


class EumplusSource:
    name = "eumplus"

    def fetch(
        self,
        client: HttpClient,
        *,
        dates: tuple[date, ...] = SEARCH_DATES,
    ) -> list[JobOffer]:
        all_offers: list[JobOffer] = []
        for work_date in dates:
            query = urllib.parse.urlencode(
                {"date": work_date.isoformat(), "vnum": 1000, "page": 1}
            )
            all_offers.extend(
                parse_page(client.get(f"{LIST_URL}?{query}"), work_date)
            )
        return all_offers
