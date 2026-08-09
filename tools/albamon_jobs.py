#!/usr/bin/env python3
"""Export and verify jobs from an Albamon saved 맞춤알바 condition.

The script uses Albamon's read-only public BFF detail endpoints, with structured
Next.js page data as a compatibility fallback. It does not read browser cookies,
log in, or submit applications.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import html
import http.client
import json
import math
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from tools.albamon_cache import DetailCache
except ModuleNotFoundError:  # Support direct execution from the repository root.
    from albamon_cache import DetailCache


GENERAL_API_URL = "https://bff-general.albamon.com"
SITE_URL = "https://www.albamon.com"
DEFAULT_SUIT_NO = 35138482
DEFAULT_PAGE_SIZE = 5_000
DEFAULT_WORKERS = 24
REQUEST_TIMEOUT_SECONDS = 30
MAX_READ_ATTEMPTS = 3
DEFAULT_MANUAL_REVIEWS_PATH = Path(__file__).with_name("albamon_manual_reviews.json")
LATE_NIGHT_AVAILABLE_FROM = "19:00"
LATE_NIGHT_AVAILABLE_UNTIL = "02:00"
LATE_NIGHT_AVAILABILITY = (
    f"{LATE_NIGHT_AVAILABLE_FROM}~{LATE_NIGHT_AVAILABLE_UNTIL}"
)

JSON_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
    "Content-Type": "application/json",
    "albamon-domain-type": "pc",
    "User-Agent": "albamon-saved-search-export/1.0",
}
HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml",
    "User-Agent": "albamon-saved-search-export/1.0",
}

NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.DOTALL
)
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
WEEKDAY_SPLIT_RE = re.compile(r"\s*,\s*")
TERM_WEEKDAY_RE = re.compile(r"\(([월화수목금토일])\)")
FULL_KOREAN_WEEKDAY_RE = re.compile(r"([월화수목금토일])요일")
APPLICATION_RISK_RE = re.compile(
    r"방\s*사진|사진\s*제출|영상\s*데이터|행동\s*영상|얼굴\s*(?:촬영|사진)|"
    r"보험|회원\s*가입|계정\s*(?:생성|대여|공유)|수수료|소개료|선입금|"
    r"홍보\s*(?:요원|알바)|리워드|온라인\s*조사|설문\s*조사|"
    r"리뷰\s*작성|댓글\s*작성"
)
WEEKDAY_NAMES = {
    "월": "mon",
    "화": "tue",
    "수": "wed",
    "목": "thu",
    "금": "fri",
    "토": "sat",
    "일": "sun",
}


class AlbamonError(RuntimeError):
    """Raised when Albamon returns data that cannot be used safely."""


class GeneralApiClient:
    """Thread-local keep-alive client for Albamon's read-only JSON BFF."""

    def __init__(self, *, timeout: int = REQUEST_TIMEOUT_SECONDS) -> None:
        self.timeout = timeout
        self._local = threading.local()
        self._connections: set[http.client.HTTPSConnection] = set()
        self._lock = threading.Lock()
        self.requests = 0
        self.retries = 0
        self.bytes_received = 0

    def __enter__(self) -> "GeneralApiClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _connection(self) -> http.client.HTTPSConnection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = http.client.HTTPSConnection(
                "bff-general.albamon.com", timeout=self.timeout
            )
            self._local.connection = connection
            with self._lock:
                self._connections.add(connection)
        return connection

    def _discard_thread_connection(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            return
        connection.close()
        with self._lock:
            self._connections.discard(connection)
        self._local.connection = None

    def read(
        self,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        method: str | None = None,
    ) -> dict[str, Any]:
        body = (
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
            if payload is not None
            else None
        )
        request_method = method or ("POST" if payload is not None else "GET")
        last_error: Exception | None = None
        for attempt in range(1, MAX_READ_ATTEMPTS + 1):
            retry_delay = 0.0
            try:
                connection = self._connection()
                connection.request(request_method, path, body=body, headers=JSON_HEADERS)
                response = connection.getresponse()
                raw = response.read()
                if response.getheader("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                with self._lock:
                    self.requests += 1
                    self.bytes_received += len(raw)
                if response.status < 200 or response.status >= 300:
                    preview = raw.decode("utf-8", errors="replace")[:300]
                    error = AlbamonError(
                        f"Albamon returned HTTP {response.status} for {path}: {preview}"
                    )
                    retryable = response.status == 429 or response.status >= 500
                    if not retryable or attempt == MAX_READ_ATTEMPTS:
                        raise error
                    retry_after = response.getheader("Retry-After")
                    retry_delay = (
                        float(retry_after)
                        if retry_after and retry_after.isdigit()
                        else 0.0
                    )
                    last_error = error
                else:
                    decoded = json.loads(raw.decode("utf-8"))
                    if not isinstance(decoded, dict):
                        raise AlbamonError(
                            f"Albamon returned a non-object JSON response for {path}"
                        )
                    return decoded
            except (
                OSError,
                TimeoutError,
                http.client.HTTPException,
                json.JSONDecodeError,
            ) as exc:
                last_error = exc
                if attempt == MAX_READ_ATTEMPTS:
                    raise AlbamonError(f"Could not read {path}: {exc}") from exc
            self._discard_thread_connection()
            with self._lock:
                self.retries += 1
            base_delay = max(retry_delay, 0.25 * (2 ** (attempt - 1)))
            time.sleep(base_delay + random.uniform(0, 0.15))
        raise AlbamonError(f"Could not read {path}: {last_error}")

    def close(self) -> None:
        with self._lock:
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            connection.close()


@dataclass(frozen=True)
class EndTimeInfo:
    start_time: str | None
    end_time: str | None
    overnight: bool | None
    ends_before: bool | None


@dataclass(frozen=True)
class DurationAssessment:
    minimum_months: float | None
    hard_minimum: bool
    summary: str
    evidence: str
    source: str
    status: str
    minimum_days: int | None = None


def read_url(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    expect_json: bool,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> Any:
    """Read an idempotent Albamon endpoint with bounded transient retries."""

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = JSON_HEADERS if expect_json else HTML_HEADERS
    request = urllib.request.Request(url, data=data, headers=headers)

    for attempt in range(1, MAX_READ_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                content_encoding = response.headers.get("Content-Encoding")
            if content_encoding == "gzip":
                raw = gzip.decompress(raw)
            if expect_json:
                return json.loads(raw.decode("utf-8"))
            return raw.decode("utf-8")
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable or attempt == MAX_READ_ATTEMPTS:
                raise AlbamonError(f"Albamon returned HTTP {exc.code} for {url}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == MAX_READ_ATTEMPTS:
                raise AlbamonError(f"Could not read {url}: {exc}") from exc

        time.sleep(0.25 * (2 ** (attempt - 1)))

    raise AssertionError("retry loop should always return or raise")


def normalize_enums(value: Any) -> Any:
    """Convert Albamon enum objects to the key-only request representation."""

    if isinstance(value, dict):
        if {"key", "value", "description"}.issubset(value):
            return value["key"]
        return {
            key: normalize_enums(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [normalize_enums(item) for item in value if item is not None]
    return value


def fetch_saved_condition(suit_no: int) -> dict[str, Any]:
    url = (
        f"{GENERAL_API_URL}/recruit/search/condition"
        f"?recruitListType=NORMAL_SUIT&suitNo={suit_no}"
    )
    response = read_url(url, expect_json=True)
    condition = response.get("condition")
    if not isinstance(condition, dict):
        raise AlbamonError(f"Saved condition {suit_no} was not returned")
    returned_suit_no = (
        condition.get("extensionCondition", {}).get("suit", {}).get("suitNo")
    )
    if returned_suit_no != suit_no:
        raise AlbamonError(
            f"Requested saved condition {suit_no}, got {returned_suit_no!r}"
        )
    return condition


def build_search_payload(
    raw_condition: dict[str, Any],
    *,
    page: int,
    page_size: int,
    strict_weekdays: bool,
) -> dict[str, Any]:
    payload = normalize_enums(raw_condition)
    payload["pagination"] = {"page": page, "size": page_size}
    if strict_weekdays:
        payload.setdefault("condition", {})["excludeNegoWorkWeek"] = True
    return payload


def fetch_all_search_jobs(
    raw_condition: dict[str, Any],
    *,
    page_size: int,
    strict_weekdays: bool,
    api_client: GeneralApiClient | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch all normal-list pages concurrently and de-duplicate recruit IDs."""

    jobs_by_id: dict[int, dict[str, Any]] = {}
    owned_client = api_client is None
    client = api_client or GeneralApiClient()

    def fetch_page(page: int) -> tuple[int, dict[str, Any]]:
        payload = build_search_payload(
            raw_condition,
            page=page,
            page_size=page_size,
            strict_weekdays=strict_weekdays,
        )
        return page, client.read("/recruit/search", payload=payload)

    try:
        _, first_response = fetch_page(1)
        base = first_response.get("base", {})
        pagination = base.get("pagination", {})
        total_count = int(pagination.get("totalCount") or 0)
        returned_size = int(pagination.get("size") or page_size)
        total_pages = max(1, math.ceil(total_count / max(1, returned_size)))
        if total_pages > 1_000:
            raise AlbamonError("Search pagination exceeded the safety limit")
        responses = {1: first_response}
        if total_pages > 1:
            with ThreadPoolExecutor(max_workers=min(8, total_pages - 1)) as executor:
                futures = [executor.submit(fetch_page, page) for page in range(2, total_pages + 1)]
                for future in as_completed(futures):
                    page, response = future.result()
                    responses[page] = response
    finally:
        if owned_client:
            client.close()

    for page in sorted(responses):
        response = responses[page]
        base = response.get("base", {})
        page_jobs = base.get("normal", {}).get("collection", [])
        if not isinstance(page_jobs, list):
            raise AlbamonError("Search response did not contain a normal job list")
        for job in page_jobs:
            recruit_no = job.get("recruitNo")
            if isinstance(recruit_no, int):
                jobs_by_id[recruit_no] = job

    return list(jobs_by_id.values()), total_count


def parse_next_data(document: str, recruit_no: int) -> dict[str, Any]:
    match = NEXT_DATA_RE.search(document)
    if not match:
        raise AlbamonError(f"Job {recruit_no} did not contain structured page data")
    try:
        page_data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise AlbamonError(f"Job {recruit_no} contained invalid page data") from exc

    page_props = page_data.get("props", {}).get("pageProps", {})
    if page_props.get("fetchError"):
        raise AlbamonError(f"Job {recruit_no} returned {page_props['fetchError']}")
    view_data = page_props.get("data", {}).get("viewData")
    if not isinstance(view_data, dict):
        raise AlbamonError(f"Job {recruit_no} did not return detail fields")
    return view_data


def fetch_job_detail_html(recruit_no: int) -> dict[str, Any]:
    url = f"{SITE_URL}/jobs/detail/{recruit_no}"
    return parse_next_data(read_url(url, expect_json=False), recruit_no)


def _validate_detail_part(
    recruit_no: int, endpoint: str, response: dict[str, Any]
) -> dict[str, Any]:
    if endpoint == "view" and response.get("recruitNo") == recruit_no:
        return response
    if endpoint == "view/detail" and isinstance(response.get("content"), str):
        return response
    code = response.get("code") or response.get("errorCode") or "unknown"
    message = response.get("message") or response.get("errorMessage")
    if not message:
        message = f"unexpected response keys: {sorted(response)[:20]}"
    raise AlbamonError(f"Job {recruit_no} detail API failed ({code}): {message}")


def fetch_job_detail_api_part(
    client: GeneralApiClient, recruit_no: int, endpoint: str
) -> dict[str, Any]:
    payload: dict[str, Any] = {"recruitNo": recruit_no}
    if endpoint == "view":
        payload["naverInfoStat"] = "1"
    response = client.read(f"/v1/recruit/{endpoint}", payload=payload)
    return _validate_detail_part(recruit_no, endpoint, response)


def merge_job_detail_api_parts(
    recruit_no: int,
    view: dict[str, Any],
    detail: dict[str, Any],
) -> dict[str, Any]:
    merged = {**view, **detail}
    if merged.get("recruitNo") != recruit_no or not isinstance(
        merged.get("content"), str
    ):
        raise AlbamonError(f"Job {recruit_no} returned incomplete detail API fields")
    return merged


def fetch_job_detail(recruit_no: int) -> dict[str, Any]:
    """Use Albamon's compact detail APIs, with the public HTML page as fallback."""

    try:
        with GeneralApiClient() as client:
            view = fetch_job_detail_api_part(client, recruit_no, "view")
            detail = fetch_job_detail_api_part(client, recruit_no, "view/detail")
        return merge_job_detail_api_parts(recruit_no, view, detail)
    except AlbamonError:
        return fetch_job_detail_html(recruit_no)


def fetch_job_details_batch(
    jobs: list[dict[str, Any]],
    *,
    workers: int,
    on_detail: Any = None,
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Fetch both compact API parts concurrently, falling back per failed job."""

    jobs_by_id = {int(job["recruitNo"]): job for job in jobs}
    parts: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    api_errors: dict[int, list[str]] = defaultdict(list)
    results: dict[int, dict[str, Any]] = {}
    fetched_via: dict[int, str] = {}
    with GeneralApiClient() as client:
        with ThreadPoolExecutor(max_workers=min(64, max(2, workers * 2))) as executor:
            future_map = {
                executor.submit(fetch_job_detail_api_part, client, recruit_no, endpoint): (
                    recruit_no,
                    endpoint,
                )
                for recruit_no in jobs_by_id
                for endpoint in ("view", "view/detail")
            }
            for future in as_completed(future_map):
                recruit_no, endpoint = future_map[future]
                try:
                    parts[recruit_no][endpoint] = future.result()
                except Exception as exc:
                    api_errors[recruit_no].append(str(exc))
                    continue
                if len(parts[recruit_no]) == 2 and recruit_no not in results:
                    try:
                        merged = merge_job_detail_api_parts(
                            recruit_no,
                            parts[recruit_no]["view"],
                            parts[recruit_no]["view/detail"],
                        )
                    except Exception as exc:
                        api_errors[recruit_no].append(str(exc))
                        continue
                    results[recruit_no] = merged
                    fetched_via[recruit_no] = "direct_api"
                    if on_detail is not None:
                        on_detail(jobs_by_id[recruit_no], merged, "direct_api")
        request_metrics = {
            "api_requests": client.requests,
            "api_retries": client.retries,
            "api_bytes_received": client.bytes_received,
        }

    fallback_ids = [
        recruit_no
        for recruit_no in jobs_by_id
        if recruit_no not in results
        and not any(
            code in " ".join(api_errors[recruit_no])
            for code in ("RCRV005", "RCRV006")
        )
    ]
    fallback_errors: dict[int, str] = {}
    if fallback_ids:
        with ThreadPoolExecutor(max_workers=min(workers, len(fallback_ids))) as executor:
            future_map = {
                executor.submit(fetch_job_detail_html, recruit_no): recruit_no
                for recruit_no in fallback_ids
            }
            for future in as_completed(future_map):
                recruit_no = future_map[future]
                try:
                    detail = future.result()
                    results[recruit_no] = detail
                    fetched_via[recruit_no] = "html_fallback"
                    if on_detail is not None:
                        on_detail(jobs_by_id[recruit_no], detail, "html_fallback")
                except Exception as exc:
                    fallback_errors[recruit_no] = str(exc)

    errors = []
    for recruit_no in jobs_by_id:
        if recruit_no in results:
            continue
        messages = api_errors.get(recruit_no) or []
        if recruit_no in fallback_errors:
            messages.append(f"HTML fallback failed: {fallback_errors[recruit_no]}")
        errors.append(
            {
                "recruit_no": recruit_no,
                "url": f"{SITE_URL}/jobs/detail/{recruit_no}",
                "error": " | ".join(messages) or "detail fetch did not return data",
            }
        )
    errors.sort(key=lambda error: error["recruit_no"], reverse=True)
    metrics = {
        **request_metrics,
        "direct_api_jobs": sum(value == "direct_api" for value in fetched_via.values()),
        "html_fallback_jobs": sum(
            value == "html_fallback" for value in fetched_via.values()
        ),
        "detail_fetch_errors": len(errors),
    }
    return results, errors, metrics


def explicit_weekdays(work_week: Any) -> list[str]:
    if not isinstance(work_week, dict):
        return []
    value = work_week.get("value")
    if not isinstance(value, str) or not value.strip():
        return []
    return [
        day.strip().lower()
        for day in WEEKDAY_SPLIT_RE.split(value)
        if day.strip()
    ]


def is_saturday_and_sunday_only(work_week: Any) -> bool:
    weekdays = explicit_weekdays(work_week)
    return len(weekdays) == 2 and set(weekdays) == {"sat", "sun"}


def korean_weekdays(text: Any, *, parenthesized_only: bool = False) -> set[str]:
    if not isinstance(text, str) or not text.strip():
        return set()
    pattern = TERM_WEEKDAY_RE if parenthesized_only else FULL_KOREAN_WEEKDAY_RE
    return {WEEKDAY_NAMES[match] for match in pattern.findall(text)}


def detail_text_lines(value: Any) -> list[str]:
    """Preserve useful line boundaries from an employer's HTML description."""

    source = html.unescape(str(value or ""))
    source = re.sub(
        r"(?i)<br\s*/?>|</(?:p|div|tr|td|th|li|h[1-6]|table)\s*>",
        "\n",
        source,
    )
    source = re.sub(r"<[^>]+>", " ", source)
    return [
        normalized
        for line in source.splitlines()
        if (normalized := re.sub(r"\s+", " ", line).strip())
    ]


def extract_weekend_shift_options(view_data: dict[str, Any]) -> list[str]:
    """Extract selectable Sat/Sun time ranges from detailed schedule blocks."""

    lines = detail_text_lines(view_data.get("content"))
    time_range = re.compile(
        r"(?<!\d)([0-2]?\d:[0-5]\d\s*[~～-]\s*[0-2]?\d:[0-5]\d)(?!\d)"
    )
    options: list[str] = []
    for index, line in enumerate(lines):
        both_days = bool(
            re.search(
                r"(?:\(\s*토\s*[,/&·]\s*일\s*\)|"
                r"토(?:요일)?\s*(?:-|~|～|,|/|&|·|및|과)\s*일(?:요일)?)",
                line,
            )
        )
        saturday = both_days or bool(re.search(r"\(\s*토\s*\)|토요일", line))
        sunday = both_days or bool(re.search(r"\(\s*일\s*\)|일요일", line))
        if not saturday and not sunday:
            continue

        block = [line]
        for following in lines[index + 1 : index + 8]:
            if re.search(r"\([^)]*[월화수목금토일][^)]*\)", following):
                break
            if time_range.search(following):
                block.append(following)
                continue
            if len(block) > 1:
                break
        ranges = [
            re.sub(r"\s+", "", match)
            for match in time_range.findall(" ".join(block))
        ]
        label = "토·일" if both_days else "토" if saturday else "일"
        for work_time in ranges:
            rendered = f"{label} {work_time}"
            if rendered not in options:
                options.append(rendered)

    combined = [option for option in options if option.startswith("토·일 ")]
    return combined or options


def _date_mention_pattern(target: date) -> re.Pattern[str]:
    """Match a Korean or numeric posting-date spelling without partial days."""

    year = target.year
    month = target.month
    day = target.day
    return re.compile(
        rf"(?<!\d)(?:(?:{year}\s*(?:[./-]|\uB144)\s*)?"
        rf"0?{month}\s*(?:[./-]|\uC6D4)\s*0?{day}\s*(?:\uC77C)?)(?!\d)"
    )


def assess_independent_target_dates(
    view_data: dict[str, Any], target_dates: Iterable[date]
) -> tuple[list[str], list[str]]:
    """Return target dates that the posting verifies can be worked alone.

    A fixed one-day structured term is sufficient evidence. When a posting
    advertises several target dates, it must also say that a single day/date
    can be selected. Contradictory all-days requirements always win.
    """

    targets = tuple(sorted(set(target_dates)))
    if not targets:
        return [], []
    work_period = view_data.get("workPeriod")
    one_day = enum_description(work_period) == "하루(1일)" or (
        isinstance(work_period, dict) and str(work_period.get("value") or "") == "5"
    )
    if not one_day:
        return [], []

    all_days_required = re.compile(
        r"(?:양일|전\s*일|전\s*일정|모든\s*일정|전체\s*일정)"
        r"\s*(?:모두\s*)?(?:근무|출근)\s*(?:필수)?|"
        r"(?:2\s*(?:일|틀)|3\s*일)\s*(?:연속|모두|전일)\s*"
        r"(?:근무|출근)(?:\s*필수)?"
    )
    posting_text = " ".join(
        (
            str(view_data.get("recruitTitle") or ""),
            str(view_data.get("workTermDescription") or ""),
            str(view_data.get("workWeekEtc") or ""),
            plain_text(view_data.get("content")),
        )
    )
    if all_days_required.search(posting_text):
        return [], []

    start_raw = view_data.get("workTermStartDate")
    end_raw = view_data.get("workTermEndDate")
    try:
        start_date = date.fromisoformat(str(start_raw))
        end_date = date.fromisoformat(str(end_raw))
    except (TypeError, ValueError):
        start_date = None
        end_date = None
    if start_date is not None and start_date == end_date:
        if start_date in targets:
            rendered = start_date.isoformat()
            return [rendered], [f"fixed one-day structured work term: {rendered}"]
        # A fixed structured date is authoritative; incidental dates in the
        # description (training, handover, payday) must not override it.
        return [], []

    independent_signal = re.compile(
        r"(?:1\s*\uC77C|\uD558\uB8E8)\s*(?:\uB9CC\s*)?(?:\uAC00\uB2A5|\uADFC\uBB34\s*\uAC00\uB2A5)|"
        r"\uD558\uB8E8\s*\uB9CC|\uC6D0\uD558\uB294\s*\uB0A0|\uD76C\uB9DD\s*(?:\uC77C\uC790|\uB0A0\uC9DC)|"
        r"(?:\uC77C\uC790|\uB0A0\uC9DC|\uADFC\uBB34\s*\uC77C)\s*(?:\uC120\uD0DD|\uD0DD\s*1)|"
        r"\uC120\uD0DD\s*(?:\uC77C\uC790|\uB0A0\uC9DC)|\uC911\s*(?:1\s*\uC77C|\uD558\uB8E8)|"
        r"\uD0DD\s*1|1\s*\uC77C\s*\uB2E8\uC704"
    )
    application_only = re.compile(
        r"지원|접수|마감|면접|연락|급여\s*일|"
        r"지급\s*일|정산\s*일|교육|인수\s*인계"
    )
    work_context = re.compile(
        r"\uADFC\uBB34|\uC77C\uC815|\uD589\uC0AC|\uCD9C\uADFC|\uC791\uC5C5|\uC5C5\uBB34|\uC77C\uC790|\uB0A0\uC9DC|\uC54C\uBC14|1\s*\uC77C"
    )

    text_sources: list[tuple[str, str]] = [
        ("title", str(view_data.get("recruitTitle") or "")),
        ("work term", str(view_data.get("workTermDescription") or "")),
        ("schedule note", str(view_data.get("workWeekEtc") or "")),
    ]
    text_sources.extend(
        ("detail", line) for line in detail_text_lines(view_data.get("content"))
    )
    mentioned: dict[date, list[str]] = {target: [] for target in targets}
    relevant_text: list[str] = []
    for source, raw_text in text_sources:
        text = re.sub(r"\s+", " ", raw_text).strip()
        if not text:
            continue
        matched_targets = [
            target for target in targets if _date_mention_pattern(target).search(text)
        ]
        if not matched_targets:
            continue
        if (
            source == "detail"
            and application_only.search(text)
            and not work_context.search(text)
            and not independent_signal.search(text)
        ):
            continue
        relevant_text.append(text)
        for target in matched_targets:
            snippet = f"{source}: {text[:180]}"
            if snippet not in mentioned[target]:
                mentioned[target].append(snippet)

    found = [target for target in targets if mentioned[target]]
    if not found:
        return [], []
    combined = " ".join(relevant_text)
    if all_days_required.search(combined):
        return [], []

    selectable = bool(independent_signal.search(combined))
    range_between_targets = False
    for left in targets:
        for right in targets:
            if left >= right:
                continue
            left_pattern = _date_mention_pattern(left).pattern
            right_pattern = _date_mention_pattern(right).pattern
            if re.search(
                rf"{left_pattern}\s*(?:~|\uFF5E|-|\uBD80\uD130)\s*{right_pattern}",
                combined,
            ):
                range_between_targets = True
                break
        if range_between_targets:
            break
    if (len(found) > 1 or range_between_targets) and not selectable:
        return [], []

    options = [target.isoformat() for target in found]
    evidence = [
        f"one-day posting; {snippet}"
        for target in found
        for snippet in mentioned[target][:1]
    ]
    if selectable:
        evidence.append("posting explicitly permits choosing one date/day")
    return options, evidence


def _clock_text(hour: str, minute: str | None) -> str | None:
    hour_value = int(hour)
    minute_value = int(minute or 0)
    if not 0 <= hour_value <= 23 or not 0 <= minute_value <= 59:
        return None
    return f"{hour_value:02d}:{minute_value:02d}"


def extract_shift_time_ranges(view_data: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Extract credible advertised shift ranges, including overnight options."""

    ranges: list[tuple[str, str, str]] = []

    def add(start: str | None, end: str | None, evidence: str) -> None:
        if (
            not start
            or not end
            or parse_clock_minutes(start) is None
            or parse_clock_minutes(end) is None
        ):
            return
        if any(existing[:2] == (start, end) for existing in ranges):
            return
        value = (start, end, re.sub(r"\s+", " ", evidence).strip())
        ranges.append(value)

    structured_start = view_data.get("workStartTime")
    structured_end = view_data.get("workEndTime")
    add(
        structured_start if isinstance(structured_start, str) else None,
        structured_end if isinstance(structured_end, str) else None,
        "structured work time",
    )

    colon_range = re.compile(
        r"(?<!\d)([0-2]?\d)\s*:\s*([0-5]\d)\s*[~～-]\s*"
        r"([0-2]?\d)\s*:\s*([0-5]\d)(?!\d)"
    )
    korean_range = re.compile(
        r"(?<!\d)([0-2]?\d)\s*시\s*(?:(\d{1,2})\s*분)?\s*[~～-]\s*"
        r"(?:새벽\s*)?([0-2]?\d)\s*시\s*(?:(\d{1,2})\s*분)?"
    )
    incidental_context = re.compile(
        r"면접|연락|문의|접수\s*시간|출금|정산|영업\s*시간|"
        r"안주\s*마감|주문\s*마감|라스트\s*오더|교육|인수\s*인계"
    )
    break_context = re.compile(r"휴게|식사\s*시간|점심\s*시간|브레이크")
    schedule_signal = re.compile(
        r"근무\s*(?:시간|조|타임|일정)|파트|미들|서브|평일|주말|"
        r"[월화수목금토일]요일|(?:오픈|마감)\s*(?:조|파트|알바|근무|[-:：0-9])"
    )
    text_candidates = [
        (str(view_data.get(field) or ""), str(view_data.get(field) or ""))
        for field in (
            "recruitTitle",
            "workTimeDetail",
            "workTimeOption",
            "workTimeOpt",
            "workWeekEtc",
        )
    ]
    detail_lines = detail_text_lines(view_data.get("content"))
    text_candidates.extend(
        (
            line,
            " ".join(detail_lines[max(0, index - 2) : index + 1]),
        )
        for index, line in enumerate(detail_lines)
    )
    for raw_text, raw_context in text_candidates:
        text = re.sub(r"\s+", " ", raw_text).strip()
        context = re.sub(r"\s+", " ", raw_context).strip()
        if not text or incidental_context.search(context):
            continue
        if break_context.search(context) and not schedule_signal.search(text):
            continue
        for match in colon_range.finditer(text):
            add(
                _clock_text(match.group(1), match.group(2)),
                _clock_text(match.group(3), match.group(4)),
                text[:180],
            )
        for match in korean_range.finditer(text):
            add(
                _clock_text(match.group(1), match.group(2)),
                _clock_text(match.group(3), match.group(4)),
                text[:180],
            )
    return ranges


def shift_fits_availability(
    start_time: str,
    end_time: str,
    *,
    available_from: str = LATE_NIGHT_AVAILABLE_FROM,
    available_until: str = LATE_NIGHT_AVAILABLE_UNTIL,
) -> bool:
    """Return whether a shift is wholly contained in an availability window."""

    start = parse_clock_minutes(start_time)
    end = parse_clock_minutes(end_time)
    window_start = parse_clock_minutes(available_from)
    window_end = parse_clock_minutes(available_until)
    if None in (start, end, window_start, window_end):
        return False
    assert start is not None and end is not None
    assert window_start is not None and window_end is not None
    window_overnight = window_end <= window_start
    absolute_window_end = window_end + (24 * 60 if window_overnight else 0)
    absolute_start = start
    if window_overnight and start < window_end:
        absolute_start += 24 * 60
    absolute_end = end
    if absolute_end <= start or absolute_end < absolute_start:
        absolute_end += 24 * 60
    return window_start <= absolute_start < absolute_end <= absolute_window_end


def assess_late_night_compatible(
    view_data: dict[str, Any],
    *,
    available_from: str = LATE_NIGHT_AVAILABLE_FROM,
    available_until: str = LATE_NIGHT_AVAILABLE_UNTIL,
) -> tuple[bool, list[str]]:
    """Find advertised shifts that fit fully inside the user's night window."""

    matches = [
        f"{start}~{end} — {evidence}"
        for start, end, evidence in extract_shift_time_ranges(view_data)
        if shift_fits_availability(
            start,
            end,
            available_from=available_from,
            available_until=available_until,
        )
    ]
    return bool(matches), matches


def assess_saturday_and_sunday_only(view_data: dict[str, Any]) -> tuple[bool, str]:
    """Assess the detail page, rejecting known contradictory schedule signals."""

    explicit_days = explicit_weekdays(view_data.get("workWeek"))
    if explicit_days:
        matches = len(explicit_days) == 2 and set(explicit_days) == {"sat", "sun"}
        return matches, "explicit weekdays" if matches else "different explicit weekdays"

    work_days = view_data.get("workDays")
    work_days_value = work_days.get("value") if isinstance(work_days, dict) else None
    work_days_description = enum_description(work_days)
    weekend_field = work_days_value == "40" or "주말" in work_days_description
    if not weekend_field:
        return False, "not marked as weekend work"

    week_notes = view_data.get("workWeekEtc") or ""
    note_days = korean_weekdays(week_notes)
    if note_days and note_days != {"sat", "sun"}:
        return False, f"weekday note contradicts weekend field: {week_notes}"

    term = view_data.get("workTermDescription") or ""
    term_days = korean_weekdays(term, parenthesized_only=True)
    if len(term_days) == 1:
        only_day = next(iter(term_days))
        return False, f"dated term contains only {only_day}"
    if term_days and not term_days.issubset({"sat", "sun"}):
        return False, "dated term contains a weekday"

    title = str(view_data.get("recruitTitle") or "")
    if re.search(r"주말\s*(?:및|/|·|,|&|와)\s*평일|평일\s*(?:및|/|·|,|&|과)\s*주말", title):
        return False, "title includes both weekend and weekday work"
    if "금요일" in title and not ("토요일" in title and "일요일" in title):
        return False, "title specifies Friday"

    description = plain_text(view_data.get("content"))
    schedule_line_match = re.search(
        r"근무\s*요일\s*[:：]?\s*(.{1,60}?)(?=\s*근무\s*시간|"
        r"\s*복리\s*후생|\s*지원\s*조건|$)",
        description,
    )
    if schedule_line_match:
        schedule_line = schedule_line_match.group(1).strip()
        stated_days = korean_weekdays(schedule_line)
        shorthand_days = {
            WEEKDAY_NAMES[day]
            for day in re.findall(
                r"(?<![가-힣])([월화수목금토일])(?=[,/·()\s]|$)",
                schedule_line,
            )
        }
        stated_days.update(shorthand_days)
        if stated_days and not stated_days.issubset({"sat", "sun"}):
            return False, f"posting content states different weekdays: {schedule_line}"

    return True, "weekend (Sat, Sun) detail field"


def assess_weekend_compatible(view_data: dict[str, Any]) -> tuple[bool, str]:
    """Accept a verified Saturday or Sunday option without requiring both days."""

    exact, basis = assess_saturday_and_sunday_only(view_data)
    work_days = view_data.get("workDays")
    work_days_value = work_days.get("value") if isinstance(work_days, dict) else None
    work_days_description = enum_description(work_days)
    negotiable = work_days_value == "50" or "협의" in work_days_description
    note = str(view_data.get("workWeekEtc") or "")
    description = plain_text(view_data.get("content"))
    title = str(view_data.get("recruitTitle") or "")
    content = f"{title} {description}".strip()
    weekend_text = content
    schedule_text = f"{note} {description}".strip()
    weekend_shift_options = extract_weekend_shift_options(view_data)
    one_day = (
        enum_description(view_data.get("workPeriod")) == "하루(1일)"
        or (
            isinstance(view_data.get("workPeriod"), dict)
            and view_data["workPeriod"].get("value") == "5"
        )
    )
    selectable_one_day = bool(
        one_day
        and weekend_shift_options
        and re.search(
            r"1일\s*가능|희망\s*(?:요일|일자)|원하는\s*날|"
            r"(?:요일|일자|근무일)\s*선택|선택\s*(?:요일|일자)",
            content,
        )
    )
    explicit_days = explicit_weekdays(view_data.get("workWeek"))
    explicit_weekend_only = bool(
        explicit_days
        and set(explicit_days).issubset({"sat", "sun"})
    )

    dated_schedule = re.search(
        r"(?:일정|근무\s*(?:일|일자)|행사\s*일자)\s*[:：]?"
        r"([^.!?\n]{0,100})",
        schedule_text,
    )
    dated_days: set[str] = set()
    if dated_schedule:
        dated_days = korean_weekdays(
            dated_schedule.group(1), parenthesized_only=True
        )
        if dated_days and not dated_days.intersection({"sat", "sun"}):
            return False, (
                "dated schedule does not include Saturday or Sunday: "
                + dated_schedule.group(0).strip()
            )

    term_days = korean_weekdays(
        view_data.get("workTermDescription") or "", parenthesized_only=True
    )
    term_start = view_data.get("workTermStartDate")
    term_end = view_data.get("workTermEndDate")
    fixed_weekend_date = False
    if isinstance(term_start, str) and term_start == term_end:
        try:
            fixed_weekend_date = date.fromisoformat(term_start).weekday() >= 5
        except ValueError:
            fixed_weekend_date = False
    if term_days == {"sat", "sun"} and isinstance(term_start, str) and isinstance(term_end, str):
        try:
            adjacent_weekend_dates = (
                date.fromisoformat(term_end) - date.fromisoformat(term_start)
            ).days == 1
        except ValueError:
            adjacent_weekend_dates = False
        if adjacent_weekend_dates:
            return True, "fixed adjacent Saturday-Sunday work term"

    schedule_line_days: set[str] = set()
    schedule_line_match = re.search(
        r"근무\s*요일\s*[:：]?\s*(.{1,80}?)(?=\s*근무\s*시간|"
        r"\s*복리\s*후생|\s*지원\s*조건|$)",
        description,
    )
    if schedule_line_match:
        schedule_line = schedule_line_match.group(1).strip()
        schedule_line_days = korean_weekdays(schedule_line)
        schedule_line_days.update(
            WEEKDAY_NAMES[day]
            for day in re.findall(
                r"(?<![가-힣\d])([월화수목금토일])(?=[,/·()\s]|$)",
                schedule_line,
            )
        )
        if (
            schedule_line_days
            and not schedule_line_days.intersection({"sat", "sun"})
        ):
            return False, (
                "negotiable posting's schedule line has no Saturday or Sunday: "
                + schedule_line
            )

    required_named_days = re.search(
        r"((?:(?:월|화|수|목|금|토|일)(?:요일)?\s*[,/·]?\s*){2,7})"
        r"근무\s*가능한\s*분",
        schedule_text,
    )
    if required_named_days:
        required_days = korean_weekdays(required_named_days.group(0))
        required_days.update(
            WEEKDAY_NAMES[day]
            for day in re.findall(
                r"(?<![가-힣\d])([월화수목금토일])(?=[,/·()\s]|$)",
                required_named_days.group(0),
            )
        )
        if required_days.difference({"sat", "sun"}):
            return False, (
                "posting requires named weekday availability in addition to weekend: "
                + required_named_days.group(0).strip()
            )

    weekend_exclusion = re.search(
        r"주말\s*제외|토(?:요일)?\s*[,/·\s]+\s*일(?:요일)?\s*제외",
        content,
    )
    if weekend_exclusion:
        return False, (
            "posting explicitly excludes weekend work: "
            + evidence_excerpt(content, weekend_exclusion)
        )

    weekday_training = re.search(
        r"(?:(?:첫\s*주|사전|업무\s*숙지)[^.!?]{0,45}평일\s*\d+일"
        r"[^.!?]{0,30}(?:출근|교육|근무)|평일\s*교육\s*가능자)",
        schedule_text,
    )
    if weekday_training:
        return False, (
            "required weekday training/attendance: "
            + evidence_excerpt(schedule_text, weekday_training)
        )

    required_multi_day = re.search(
        r"(?:주\s*(?:2\s*[~～-]\s*)?(?:3|4|5|6|7)\s*일(?:\s*이상)?[^.!?]{0,35}"
        r"(?:고정\s*)?(?:스케줄\s*)?근무|"
        r"주\s*(?:3|4|5|6|7)\s*일(?:\s*이상)?(?=\s|$)|"
        r"(?:금|목)(?:요일)?\s*[~～-]\s*일(?:요일)?|"
        r"금\s*[,/·]?\s*토\s*[,/·]?\s*일)",
        schedule_text,
    )
    friday_weekend_required = re.search(
        r"금(?:요일)?\s*[,/·]?\s*토(?:요일)?\s*[,/·]?\s*일(?:요일)?"
        r"(?:\s*(?:근무자|근무|출근))?",
        schedule_text,
    )
    friday_weekend_has_sat_sun_alternative = re.search(
        r"(?:금\s*[,/·]?\s*토\s*[,/·]?\s*일|금토일)\s*"
        r"(?:or|또는)\s*토\s*[,/·]?\s*일",
        schedule_text,
        re.IGNORECASE,
    )
    friday_weekend_is_selectable = re.search(
        r"금(?:요일)?\s*[,/·]?\s*토(?:요일)?\s*[,/·]?\s*일(?:요일)?"
        r"[^.!?]{0,35}(?:중\s*)?선택(?:\s*요일|요일)?\s*(?:지원)?\s*가능",
        schedule_text,
    )
    if (
        friday_weekend_required
        and not friday_weekend_has_sat_sun_alternative
        and not friday_weekend_is_selectable
        and not selectable_one_day
    ):
        return False, (
            "posting requires Friday in addition to Sat/Sun: "
            + evidence_excerpt(schedule_text, friday_weekend_required)
        )
    separate_weekend_role = re.search(
        r"(?:주말\s*(?:오전|오후|마감|오픈|풀타임|파트|알바)|"
        r"(?:평일\s*[/·및&]\s*주말|주말\s*[/·및&]\s*평일)"
        r"[^.!?]{0,20}(?:모두\s*)?(?:구|모집)|"
        r"근무\s*요일[^.!?]{0,80}주말[_\s-]*토일|"
        r"월\s*-\s*금\s*\(\s*\d+명\s*\)\s*/\s*"
        r"토\s*-\s*일\s*\(\s*\d+명\s*\)|"
        r"(?:금\s*[,/·]?\s*토\s*[,/·]?\s*일|금토일)\s*"
        r"(?:or|또는)\s*토일|"
        r"근무\s*요일[^.!?]{0,100}(?:선택\s*지원|선택\s*요일|요일\s*선택)\s*가능)",
        schedule_text,
        re.IGNORECASE,
    )
    if (
        required_multi_day
        and not separate_weekend_role
        and not friday_weekend_is_selectable
        and not selectable_one_day
    ):
        return False, (
            "posting requires a multi-day schedule beyond Sat/Sun: "
            + evidence_excerpt(schedule_text, required_multi_day)
        )

    mixed_required_days = set(explicit_days).difference({"sat", "sun"}) or (
        schedule_line_days.difference({"sat", "sun"})
        if schedule_line_days.intersection({"sat", "sun"})
        else set()
    )
    if (
        mixed_required_days
        and not selectable_one_day
        and not separate_weekend_role
        and not friday_weekend_is_selectable
        and not fixed_weekend_date
    ):
        return False, "posting requires weekday availability in addition to weekend"

    if selectable_one_day:
        return True, "detail offers selectable one-day Saturday/Sunday shift slots"
    if exact:
        return True, basis
    if explicit_weekend_only:
        return True, "explicit Saturday or Sunday schedule"
    if fixed_weekend_date:
        return True, "fixed one-day Saturday or Sunday work term"
    if term_days and term_days.issubset({"sat", "sun"}):
        return True, "dated work term offers Saturday or Sunday"
    if dated_days.intersection({"sat", "sun"}):
        return True, "dated schedule offers Saturday or Sunday work"
    if not negotiable:
        return False, basis
    if re.search(r"원하는\s*날\s*출근\s*가능", note):
        return True, f"negotiable: {note}"

    if re.search(
        r"(?:주말\s*\(?\s*토\s*[,/·\s]+\s*일\s*\)?|"
        r"토요일\s*(?:과|및|,|/|·|\s)+\s*일요일|"
        r"토\s*[,/·&]\s*일)",
        weekend_text,
    ):
        return True, "negotiable schedule explicitly offers Saturday and Sunday"
    if re.search(
        r"(?:평일\s*[/·및&]\s*주말|주말\s*[/·및&]\s*평일)"
        r"[^.!?]{0,20}(?:모두\s*)?(?:구|모집)",
        content,
    ):
        return True, "negotiable posting explicitly recruits a separate weekend role"
    if "주말" in title or re.search(r"매주\s*주말|주말\s*근무", content):
        return True, "negotiable schedule explicitly offers weekend work"
    if re.search(r"(?:토|일)(?:요일)?\s*(?:하루|1일|근무|알바)", content):
        return True, "negotiable schedule explicitly offers Saturday or Sunday"
    return False, "negotiable schedule has no verified weekend option"


def plain_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def application_risk_signals(view_data: dict[str, Any]) -> list[str]:
    """Retain compact safety flags without copying the entire description."""

    text = " ".join(
        (
            str(view_data.get("recruitTitle") or ""),
            plain_text(view_data.get("content")),
        )
    )
    return sorted({match.group(0).strip() for match in APPLICATION_RISK_RE.finditer(text)})


def detail_adjusted_salary(
    search_job: dict[str, Any], view_data: dict[str, Any]
) -> tuple[Any, list[str], str]:
    """Use the unconditional detail rate when a headline includes weekly pay."""

    displayed = search_job.get("pay") or view_data.get("salary") or ""
    notes = string_list(view_data.get("payEtcInfo"))
    if enum_description(view_data.get("salaryType")) != "시급":
        return displayed, notes, ""
    description = plain_text(view_data.get("content"))
    base_match = re.search(
        r"(?:1일\s*)?시급\s*[:：]?\s*([0-9][0-9,]*)\s*원",
        description,
    )
    weekly_pay_match = re.search(
        r"주\s*15\s*시간\s*이상[^.!?]{0,90}?"
        r"시급\s*[:：]?\s*([0-9][0-9,]*)\s*원[^.!?]{0,35}?주휴",
        description,
    )
    if not base_match or not weekly_pay_match:
        return displayed, notes, ""
    base_pay = int(base_match.group(1).replace(",", ""))
    weekly_pay = int(weekly_pay_match.group(1).replace(",", ""))
    structured_digits = re.sub(r"\D", "", str(view_data.get("salary") or displayed))
    structured_pay = int(structured_digits) if structured_digits else 0
    if not (0 < base_pay < weekly_pay and weekly_pay == structured_pay):
        return displayed, notes, ""
    caution = (
        f"기본 1일 시급 {base_pay:,}원; {weekly_pay:,}원은 "
        "주 15시간 이상 주휴 포함 조건"
    )
    if caution not in notes:
        notes.append(caution)
    return f"{base_pay:,}원", notes, evidence_excerpt(description, weekly_pay_match)


def evidence_excerpt(text: str, match: re.Match[str], *, radius: int = 55) -> str:
    """Return a short, auditable excerpt around a content match."""

    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"


def load_manual_reviews(path: Path | None) -> dict[int, dict[str, Any]]:
    """Load human-reviewed evidence for descriptions rendered only as images."""

    if path is None or not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise AlbamonError(f"Could not read manual reviews from {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise AlbamonError(f"Manual reviews must be a JSON object: {path}")
    reviews: dict[int, dict[str, Any]] = {}
    for recruit_no, review in raw.items():
        if isinstance(review, dict):
            try:
                reviews[int(recruit_no)] = review
            except (TypeError, ValueError):
                continue
    return reviews


def assess_driving_license_requirement(
    view_data: dict[str, Any],
) -> tuple[bool, str]:
    """Conservatively reject explicit licences and jobs whose duty is driving."""

    title = str(view_data.get("recruitTitle") or "")
    if re.search(r"운전자|발렛|대리운전|라이더|배달\s*대행", title):
        return True, "job title requires driving/valet work"

    skills = string_list(view_data.get("skill"))
    driving_skills = [
        skill
        for skill in skills
        if re.search(r"발렛|대리운전|차량\s*운전", skill)
    ]
    if driving_skills:
        return True, f"job duties include {', '.join(driving_skills)}"

    description = plain_text(view_data.get("content"))
    # A driver's licence is often listed as an alternative identity document,
    # not as permission to drive for the job.
    description_for_requirement = re.sub(
        r"신분증\s*(?:또는|혹은|/|이나)\s*운전\s*면허(?:증)?\s*(?:준비|지참)?",
        "",
        description,
    )
    license_match = re.search(
        r"운전\s*면허(?:증)?(?:\s*(?:소지|필수|보유))?|"
        r"(?:1|2)종\s*(?:보통|대형)?\s*면허",
        description_for_requirement,
    )
    if license_match:
        return True, f"description mentions {license_match.group(0).strip()}"

    return False, "no driving licence requirement found"


def demographic_compatible(view_data: dict[str, Any], *, age: int) -> tuple[bool, str]:
    """Verify that a male applicant of the requested age is permitted."""

    gender = view_data.get("gender")
    gender_value = gender.get("value") if isinstance(gender, dict) else None
    gender_description = enum_description(gender)
    if gender_value not in (None, "", "0", "M") and "남" not in gender_description:
        return False, f"gender restriction: {gender_description or gender_value}"
    if "여자" in gender_description and "무관" not in gender_description:
        return False, f"gender restriction: {gender_description}"

    start_age = view_data.get("startAge")
    end_age = view_data.get("endAge")
    try:
        minimum = int(start_age or 0)
        maximum = int(end_age or 0)
    except (TypeError, ValueError):
        minimum = maximum = 0
    if minimum and age < minimum:
        return False, f"minimum age is {minimum}"
    if maximum and age > maximum:
        return False, f"maximum age is {maximum}"
    return True, f"male age {age} is allowed"


def education_compatible(view_data: dict[str, Any]) -> tuple[bool, str]:
    """Accept only unrestricted education, matching the saved user filter."""

    levels = string_list(view_data.get("educationLevels"))
    if not levels:
        return True, "no structured education restriction"
    if any("학력무관" in level for level in levels):
        return True, "education unrestricted"
    return False, f"education requirement: {', '.join(levels)}"


def assess_logistics_job(
    search_job: dict[str, Any], view_data: dict[str, Any]
) -> tuple[bool, str, str, str, str]:
    """Classify warehouse/logistics ads and whether cold work is advertised."""

    title = str(view_data.get("recruitTitle") or search_job.get("recruitTitle") or "")
    company = str(
        view_data.get("recruitCompanyName") or search_job.get("companyName") or ""
    )
    parts = " ".join(string_list(view_data.get("part")) or search_job.get("parts", []))
    content = plain_text(view_data.get("content"))
    headline = " ".join((title, company, parts))
    combined = " ".join((headline, content))
    is_coupang = bool(search_job.get("isCoupang")) or "쿠팡" in combined
    is_kurly = bool(re.search(r"(?:마켓\s*)?컬리|Kurly", combined, re.IGNORECASE))
    is_bmart = bool(re.search(r"배민\s*B\s*마트|B\s*마트", combined, re.IGNORECASE))
    logistics_terms = re.search(
        r"물류|피킹|상하차|입출고|창고|허브|Sub\s*Hub|Fulfillment|배송\s*캠프",
        headline,
        re.IGNORECASE,
    )
    # Albamon occasionally attaches a generic warehouse category to casting
    # notices even though the advertised work is entirely filming/extra work.
    # Keep those in the normal jobs section unless the title/company itself or
    # a known logistics brand identifies an actual logistics role.
    media_role = re.search(r"보조\s*출연|단역|촬영\s*스태프|엑스트라", title, re.IGNORECASE)
    title_company_logistics = re.search(
        r"물류|피킹|상하차|입출고|창고|허브|Sub\s*Hub|Fulfillment|배송\s*캠프",
        f"{title} {company}",
        re.IGNORECASE,
    )
    if (
        media_role
        and not (is_coupang or is_kurly or is_bmart)
        and not title_company_logistics
    ):
        return False, "", "", "", ""
    if not (is_coupang or is_kurly or is_bmart or logistics_terms):
        return False, "", "", "", ""

    if is_coupang:
        category = "Coupang Logistics Services"
        basis = "Coupang listing / parcel sorting or sub-hub work"
    elif is_kurly:
        category = "Market Kurly logistics"
        basis = "Market Kurly warehouse/logistics wording"
    elif is_bmart:
        category = "Baemin B-Mart micro-fulfilment"
        basis = "B-Mart daily picking/packing crew"
    else:
        category = "Other logistics/warehouse"
        basis = f"logistics wording: {logistics_terms.group(0)}"

    cold_match = re.search(r"냉장|냉동|저온|콜드|신선\s*(?:센터|물류)", combined)
    if cold_match:
        cold_status = "Cold/chilled/frozen work is explicitly advertised"
        cold_evidence = evidence_excerpt(combined, cold_match)
    elif is_bmart:
        cold_status = (
            "Cold/chilled/frozen handling is part of the job; the reviewed B-Mart "
            "campaign template says refrigerated/frozen products must be checked"
        )
        cold_evidence = (
            "Manually reviewed B-Mart campaign image: 냉장/냉동 상품을 취급하는 "
            "업무; 저온 근무가 어려운 분은 근무가 어려움"
        )
    elif is_coupang:
        cold_status = (
            "Not advertised as cold storage; reviewed ad describes general "
            "Sub Hub/FC sorting in a cooled/heated indoor workplace"
        )
        cold_evidence = "여름에는 COOL하게, 겨울에는 HOT하게 / Sub Hub 분류 업무"
    else:
        cold_status = "No cold-storage wording found"
        cold_evidence = ""
    return True, category, basis, cold_status, cold_evidence


def structured_month_or_longer(work_period: Any) -> bool:
    """Return whether Albamon's broad structured bucket starts at one month."""

    if not isinstance(work_period, dict):
        return False
    value = work_period.get("value")
    try:
        if value is not None:
            return int(value) >= 30
    except (TypeError, ValueError):
        pass
    description = str(work_period.get("description") or "")
    return bool(re.search(r"(?:1|3|6)개월|1년", description))


def assess_duration_requirement(
    view_data: dict[str, Any],
    manual_review: dict[str, Any] | None = None,
) -> DurationAssessment:
    """Verify the actual minimum commitment from readable posting content.

    Albamon's structured ``workPeriod`` bucket is intentionally treated only as
    a triage hint: posters frequently select ``1개월~3개월`` while writing a
    shorter, negotiable, or longer period in the description itself.
    """

    manual_duration = (manual_review or {}).get("duration")
    if isinstance(manual_duration, dict):
        minimum = manual_duration.get("minimum_months")
        return DurationAssessment(
            float(minimum) if isinstance(minimum, (int, float)) else None,
            bool(manual_duration.get("hard_minimum")),
            str(manual_duration.get("summary") or "Manual image review"),
            str(manual_duration.get("evidence") or ""),
            str(manual_duration.get("source") or "manually reviewed posting images"),
            str(manual_duration.get("status") or "verified"),
        )

    description = plain_text(view_data.get("content"))
    title = str(view_data.get("recruitTitle") or "")
    required_question_text = " ".join(
        str(question.get("title") or "").strip()
        for question in (view_data.get("previousQuestion") or [])
        if isinstance(question, dict)
        and question.get("required")
        and str(question.get("title") or "").strip()
    )
    content = f"{title} {description} {required_question_text}".strip()
    work_period = view_data.get("workPeriod")
    structured_label = enum_description(work_period)

    regular_employee_title = re.search(r"정규직\s*(?:모집|채용)", title)
    if regular_employee_title:
        return DurationAssessment(
            None,
            False,
            "No numeric minimum; title recruits a regular employee",
            evidence_excerpt(content, regular_employee_title),
            "posting title",
            "non_numeric_requirement",
        )

    title_exact_month = re.search(
        r"(?:^|[\[(/·\s])((?:\d+(?:\.\d+)?)\s*개(?:월|윌))(?:[\]),/·\s]|$)",
        title,
    )
    if title_exact_month:
        months_match = re.search(r"\d+(?:\.\d+)?", title_exact_month.group(1))
        assert months_match is not None
        months = float(months_match.group(0))
        return DurationAssessment(
            months,
            True,
            f"Title states {months:g} month{'s' if months != 1 else ''}",
            evidence_excerpt(content, title_exact_month),
            "posting title",
            "verified",
        )

    title_month_range = re.search(
        r"(?<![\d/.])(\d{1,2})\s*[~～]\s*(\d{1,2})월(?:\s*주말)?",
        title,
    )
    if title_month_range:
        start_month = int(title_month_range.group(1))
        end_month = int(title_month_range.group(2))
        if 1 <= start_month <= 12 and 1 <= end_month <= 12:
            months = ((end_month - start_month) % 12) + 1
            return DurationAssessment(
                float(months),
                True,
                f"Title states a fixed {months}-month calendar range",
                evidence_excerpt(content, title_month_range),
                "posting title",
                "verified",
            )

    minimum_patterns = (
        re.compile(r"최소\s*(\d+(?:\.\d+)?)\s*개(?:월|윌)(?:\s*이상)?"),
        re.compile(r"(\d+(?:\.\d+)?)\s*개(?:월|윌)\s*이상"),
        re.compile(r"(\d+(?:\.\d+)?)\s*월개(?:월|윌)\s*이상"),
    )
    matches = [
        match
        for pattern in minimum_patterns
        for match in pattern.finditer(content)
    ]
    if matches:
        # The largest explicit minimum is safest when a page repeats the rule.
        match = max(matches, key=lambda item: float(item.group(1)))
        months = float(match.group(1))
        return DurationAssessment(
            months,
            True,
            f"Minimum {months:g} month{'s' if months != 1 else ''}",
            evidence_excerpt(content, match),
            "posting text",
            "verified",
        )

    year_match = re.search(r"(\d+(?:\.\d+)?)\s*년\s*이상", content)
    if year_match:
        months = float(year_match.group(1)) * 12
        return DurationAssessment(
            months,
            True,
            f"Minimum {months:g} months",
            evidence_excerpt(content, year_match),
            "posting text",
            "verified",
        )

    yearly_contract_match = re.search(
        r"최초\s*입사일\s*부터\s*(\d+(?:\.\d+)?)\s*년\s*단위\s*계약",
        content,
    )
    if yearly_contract_match:
        months = float(yearly_contract_match.group(1)) * 12
        return DurationAssessment(
            months,
            True,
            f"Minimum {months:g} months (contract term)",
            evidence_excerpt(content, yearly_contract_match),
            "posting text",
            "verified",
        )

    initial_contract_match = re.search(
        r"최초\s*계약\s*(\d+(?:\.\d+)?)\s*개(?:월|윌)", content
    )
    if initial_contract_match:
        months = float(initial_contract_match.group(1))
        return DurationAssessment(
            months,
            True,
            f"Initial contract: {months:g} months",
            evidence_excerpt(content, initial_contract_match),
            "posting text",
            "verified",
        )

    probation_commitment_match = re.search(
        r"(\d+(?:\.\d+)?)\s*개월\s*수습[^.!?]{0,45}"
        r"(?:이후|후|연장)",
        content,
    )
    if probation_commitment_match:
        months = float(probation_commitment_match.group(1))
        return DurationAssessment(
            months,
            True,
            f"Initial probation/commitment: {months:g} month"
            f"{'s' if months != 1 else ''}",
            evidence_excerpt(content, probation_commitment_match),
            "posting text",
            "verified",
        )

    event_month_range = re.search(
        r"(?:팝업\s*행사|팝업).{0,140}?\(\s*"
        r"(\d+(?:\.\d+)?)\s*[~～-]\s*(\d+(?:\.\d+)?)\s*개월\s*\)",
        content,
    )
    if event_month_range:
        lower = float(event_month_range.group(1))
        upper = float(event_month_range.group(2))
        return DurationAssessment(
            lower,
            True,
            f"Event runs {lower:g}–{upper:g} months (minimum {lower:g})",
            evidence_excerpt(content, event_month_range),
            "posting text",
            "verified",
        )

    title_contract_match = re.search(
        r"(\d+(?:\.\d+)?)\s*개월\s*계약직", title
    )
    if title_contract_match:
        months = float(title_contract_match.group(1))
        return DurationAssessment(
            months,
            True,
            f"Fixed {months:g}-month contract",
            evidence_excerpt(content, title_contract_match),
            "posting title",
            "verified",
        )

    full_date_match = re.search(
        r"(?:근무\s*(?:기간|일)|계약\s*기간|기간)[^.!?]{0,70}?"
        r"'?\d{2,4}[./-](\d{1,2})[./-](\d{1,2})\s*[~～]\s*"
        r"'?\d{2,4}[./-](\d{1,2})[./-](\d{1,2})",
        content,
    )
    if full_date_match:
        start_month, start_day, end_month, end_day = (
            int(full_date_match.group(index)) for index in range(1, 5)
        )
        try:
            start_date = date(2000, start_month, start_day)
            end_year = 2001 if end_month < start_month else 2000
            end_date = date(end_year, end_month, end_day)
            inclusive_days = (end_date - start_date).days + 1
        except ValueError:
            inclusive_days = 0
        if inclusive_days >= 28:
            months = max(1.0, round(inclusive_days / 30, 1))
            return DurationAssessment(
                months,
                True,
                f"Fixed {inclusive_days}-day period (approximately {months:g} month"
                f"{'s' if months != 1 else ''})",
                evidence_excerpt(content, full_date_match),
                "posting text",
                "verified",
                inclusive_days,
            )

    fixed_date_match = re.search(
        r"(?:근무\s*(?:기간|일)|계약\s*기간|기간)[^.!?]{0,70}?(?:"
        r"(\d{1,2})월\s*(\d{1,2})일\s*[~～-]\s*"
        r"(\d{1,2})월\s*(\d{1,2})일|"
        r"(\d{1,2})\s*[/.]\s*(\d{1,2})(?:\([^)]*\))?\s*[~～-]\s*"
        r"(\d{1,2})\s*[/.]\s*(\d{1,2})"
        r")",
        content,
    )
    if fixed_date_match:
        component_indexes = range(1, 5) if fixed_date_match.group(1) else range(5, 9)
        start_month, start_day, end_month, end_day = (
            int(fixed_date_match.group(index)) for index in component_indexes
        )
        try:
            start_date = date(2000, start_month, start_day)
            end_year = 2001 if end_month < start_month else 2000
            end_date = date(end_year, end_month, end_day)
            inclusive_days = (end_date - start_date).days + 1
        except ValueError:
            inclusive_days = 0
        if inclusive_days >= 28:
            months = max(1.0, round(inclusive_days / 30, 1))
            return DurationAssessment(
                months,
                True,
                f"Fixed {inclusive_days}-day period (approximately {months:g} month"
                f"{'s' if months != 1 else ''})",
                evidence_excerpt(content, fixed_date_match),
                "posting text",
                "verified",
                inclusive_days,
            )
        if inclusive_days > 0:
            return DurationAssessment(
                None,
                True,
                f"Fixed {inclusive_days}-day period",
                evidence_excerpt(content, fixed_date_match),
                "posting text",
                "verified_shorter",
                inclusive_days,
            )

    range_match = re.search(
        r"근무\s*기간\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:개(?:월|윌)\s*)?"
        r"[~～-]\s*(\d+(?:\.\d+)?)\s*개(?:월|윌)([^.!?]{0,25})",
        content,
    )
    if range_match:
        lower = float(range_match.group(1))
        upper = float(range_match.group(2))
        negotiable = bool(
            re.match(r"\s*\(?\s*(?:기간\s*)?협의", range_match.group(3))
        )
        if negotiable:
            if lower > 1:
                return DurationAssessment(
                    lower,
                    True,
                    f"{lower:g}–{upper:g} months (minimum {lower:g}; "
                    "negotiable, but no shorter term is advertised)",
                    evidence_excerpt(content, range_match),
                    "posting text",
                    "verified",
                )
            return DurationAssessment(
                None,
                False,
                f"{lower:g}–{upper:g} months, negotiable; no hard minimum verified",
                evidence_excerpt(content, range_match),
                "posting text",
                "negotiable",
            )
        return DurationAssessment(
            lower,
            True,
            f"{lower:g}–{upper:g} months (minimum {lower:g})",
            evidence_excerpt(content, range_match),
            "posting text",
            "verified",
        )

    week_to_month_match = re.search(
        r"근무\s*기간\s*[:：]?\s*\d+\s*주일?\s*[~～-]\s*"
        r"\d+\s*개월([^.!?]{0,25})",
        content,
    )
    if week_to_month_match:
        return DurationAssessment(
            None,
            False,
            "Less than one month, or negotiable up to one month",
            evidence_excerpt(content, week_to_month_match),
            "posting text",
            "verified_shorter",
        )

    exact_match = re.search(
        r"근무\s*기간\s*[:：]?\s*(?:약\s*)?(\d+(?:\.\d+)?)\s*개(?:월|윌)"
        r"(?!\s*[~～])([^.!?]{0,25})",
        content,
    )
    if exact_match:
        months = float(exact_match.group(1))
        tail = exact_match.group(2)
        if re.match(r"\s*\(?\s*(?:기간\s*)?협의", tail):
            return DurationAssessment(
                None,
                False,
                f"About {months:g} month{'s' if months != 1 else ''}, negotiable",
                evidence_excerpt(content, exact_match),
                "posting text",
                "negotiable",
            )
        return DurationAssessment(
            months,
            True,
            f"{months:g} month{'s' if months != 1 else ''}",
            evidence_excerpt(content, exact_match),
            "posting text",
            "verified",
        )

    one_month_match = re.search(
        r"(?:근무\s*기간|기간)[^.!?]{0,60}(?:최소\s*)?(?:한\s*달|한달)", content
    )
    if one_month_match:
        return DurationAssessment(
            1.0,
            True,
            "One month",
            evidence_excerpt(content, one_month_match),
            "posting text",
            "verified",
        )

    short_match = re.search(
        r"근무\s*기간\s*[:：]?\s*(\d+)\s*(일|주일?)\s*(이상|이하)?",
        content,
    )
    if short_match:
        count = int(short_match.group(1))
        days = count * (7 if short_match.group(2).startswith("주") else 1)
        upper_bound_only = short_match.group(3) == "이하"
        return DurationAssessment(
            None,
            not upper_bound_only,
            f"{count} {short_match.group(2)}"
            + (f" {short_match.group(3)}" if short_match.group(3) else ""),
            evidence_excerpt(content, short_match),
            "posting text",
            "verified_shorter",
            None if upper_bound_only else days,
        )

    required_question_long_term = re.search(
        r"(?:매주\s*주말[^.!?]{0,20})?장기(?:적으?로)?\s*근무\s*가능",
        required_question_text,
    )
    if required_question_long_term:
        return DurationAssessment(
            None,
            False,
            "No numeric minimum; required application question asks for long-term availability",
            evidence_excerpt(required_question_text, required_question_long_term),
            "required application question",
            "non_numeric_requirement",
        )

    negotiated_match = re.search(
        r"(?:근무\s*)?기간\s*[:：]?\s*(?:장기[^.!?]{0,20})?(?:협의|검토)", content
    )
    if negotiated_match:
        return DurationAssessment(
            None,
            False,
            "Negotiable; no numeric minimum verified",
            evidence_excerpt(content, negotiated_match),
            "posting text",
            "negotiable",
        )

    long_term_match = re.search(
        r"(?:책임감\s*있게\s*)?오래\s*근무\s*가능하신\s*분", content
    )
    if long_term_match:
        return DurationAssessment(
            None,
            False,
            "No numeric minimum; long-term availability required",
            evidence_excerpt(content, long_term_match),
            "posting text",
            "non_numeric_requirement",
        )

    if structured_month_or_longer(work_period) and content:
        return DurationAssessment(
            None,
            False,
            f"No numeric minimum stated in posting text (Albamon label: {structured_label})",
            "The readable posting text states no numeric minimum commitment.",
            "posting text",
            "not_stated",
        )

    if structured_month_or_longer(work_period):
        return DurationAssessment(
            None,
            False,
            f"Unverified: Albamon label says {structured_label or 'one month+'}",
            "No readable numeric minimum was found in the posting text.",
            "structured label only",
            "unverified",
        )

    return DurationAssessment(
        None,
        False,
        "No one-month minimum found",
        "No readable one-month minimum was found in the posting text.",
        "posting text",
        "not_found",
    )


def assess_hard_requirements(
    view_data: dict[str, Any],
    duration: DurationAssessment,
    manual_review: dict[str, Any] | None = None,
) -> tuple[list[str], list[str], list[str], str]:
    """Extract explicit must-haves without promoting ``우대`` preferences."""

    requirements: list[str] = []
    certificates: list[str] = []
    evidence: list[str] = []

    def add(label: str, matched: re.Match[str] | None, certificate: str | None = None) -> None:
        if label not in requirements:
            requirements.append(label)
        if certificate and certificate not in certificates:
            certificates.append(certificate)
        if matched:
            excerpt = evidence_excerpt(content, matched)
            if excerpt not in evidence:
                evidence.append(excerpt)

    content = plain_text(view_data.get("content"))
    if duration.hard_minimum and duration.minimum_months is not None:
        add(f"Minimum commitment: {duration.summary}", None)
        if duration.evidence:
            evidence.append(duration.evidence)
    elif duration.status == "non_numeric_requirement":
        add("Long-term availability (no numeric minimum stated)", None)

    driving_required, _ = assess_driving_license_requirement(view_data)
    if driving_required:
        driving_match = re.search(r"운전\s*면허(?:증)?|(?:1|2)종[^.!?]{0,15}면허", content)
        add("Korean driving licence / driving ability", driving_match, "Driving licence")

    health_patterns = (
        re.compile(r"보건증\s*(?:소지자|소지|필수)"),
        re.compile(r"보건증\s*발급\s*가능[^.!?]{0,20}(?:필수|분)"),
    )
    for pattern in health_patterns:
        for match in pattern.finditer(content):
            nearby = content[match.start() : min(len(content), match.end() + 15)]
            if "우대" not in nearby or "필수" in match.group(0):
                add("Health certificate (보건증)", match, "Health certificate (보건증)")
                break

    barista_match = re.search(
        r"바리스타\s*(?:자격증|자격)[^.!?]{0,20}(?:필수|소지)", content
    )
    if barista_match and "우대" not in content[barista_match.start() : barista_match.end() + 12]:
        add("Barista qualification", barista_match, "Barista qualification")

    lifeguard_match = re.search(
        r"(?:자격\s*[:：]?\s*)?(?:라이프\s*가드|인명\s*구조)\s*자격증"
        r"(?:\s*등\s*관련\s*자격증)?",
        content,
        re.IGNORECASE,
    )
    if lifeguard_match:
        add(
            "Lifeguard or related safety qualification",
            lifeguard_match,
            "Lifeguard or related safety qualification",
        )

    marine_licence_match = re.search(
        r"(?:동력\s*수상\s*레저\s*1급|요트\s*자격증|6급\s*항해사)"
        r"[^.!?\n]{0,100}(?:한\s*개\s*이상|자격\s*요건|소지자|지원\s*가능)",
        content,
    )
    if marine_licence_match:
        add(
            "One of: powerboat operator class 1, yacht licence, or deck officer class 6",
            marine_licence_match,
            "Powerboat/yacht/deck-officer licence",
        )

    rule_patterns = (
        ("Café work experience", r"자격\s*요건\s*[:：]?\s*카페\s*근무\s*경험자"),
        ("High-school graduation or above", r"(?:자격\s*요건\s*[:：]?\s*고졸|고교\s*졸업자\s*이상)"),
        ("Chinese proficiency", r"자격\s*요건\s*[:：]?[^.!?]{0,35}중국어\s*가능자"),
        ("English proficiency", r"영어로\s*(?:커뮤니케이션|의사\s*소통)\s*가능자"),
        ("English, Chinese, or Japanese proficiency", r"외국어\s*가능자[^.!?]{0,45}1개\s*이상\s*필수"),
        ("Must be at least 20", r"(?:연령\s*[:：]?\s*)?20세\s*이상"),
        ("Minors cannot apply", r"미성년자\s*(?:지원\s*)?불가"),
        ("Full-body photo with application", r"출석\s*대조용\s*전체\s*모습\s*사진"),
        ("Front-facing photo with application", r"얼굴\s*정면\s*사진"),
        ("Foreign student applicants cannot apply", r"외국인\s*유학생\s*지원\s*불가"),
        ("Male and at least 178 cm", r"178cm\s*이상\s*남자\s*분만\s*지원"),
        ("Availability for the full advertised schedule", r"전\s*일정\s*근무\s*가능[^.!?]{0,20}(?:분만|자만|필수)"),
        ("Weekday training availability", r"평일\s*\d+일\s*교육\s*가능자"),
        ("Wear the specified event outfit", r"근무\s*복장\s*[:：][^.!?]{0,100}"),
        ("Wear the specified event outfit", r"\*\s*의상\s*-\s*상의[^*!?]{0,150}"),
        ("Bring a plain black short-sleeved T-shirt", r"검은색\s*무지\s*반팔\s*티셔츠\s*개인\s*지참"),
        ("Wear the specified all-black work outfit", r"근무\s*복장[^.!?]{0,90}검은[^.!?]{0,90}유니폼\s*제공\s*X"),
        ("No visible tattoo unless it can be covered", r"외부로\s*보이는\s*문신[^.!?]{0,45}근무\s*불가"),
        ("No visible tattoos or piercings", r"피어싱\s*[xX][^.!?]{0,20}보이는\s*문신\s*[xX]"),
        ("Bring black dress shoes and dark socks", r"준비물\s*[:：]\s*검정\s*구두[^.!?]{0,40}(?:양말|스타킹)"),
        ("Foreign applicants: appropriate work visa and compliant hours", r"외국인의\s*경우\s*비자[^.!?]{0,80}근무시간\s*제한"),
        (
            "Café work experience",
            r"(?:메가\s*커피|카페)[^.!?]{0,35}경력자\s*(?:원함|필수|만)",
        ),
    )
    for label, pattern in rule_patterns:
        match = re.search(pattern, content)
        if match:
            add(label, match)

    title = str(view_data.get("recruitTitle") or "")
    if (
        re.search(r"경력(?:자|직)", title)
        and not re.search(r"경력\s*무관", content)
        and not re.search(r"경력(?:자|직)?[^()\n]{0,20}우대|경력\s*우대", title)
    ):
        add("Relevant experience (posting title specifies experience)", None)

    height_match = re.search(r"신장(?:이)?\s*163cm\s*이상[^.!?]{0,35}들어가지\s*않", content, re.IGNORECASE)
    if height_match:
        add("Height under 163 cm for the costume", height_match)

    manual_requirements = (manual_review or {}).get("hard_requirements", [])
    if isinstance(manual_requirements, list):
        for requirement in manual_requirements:
            if isinstance(requirement, str) and requirement not in requirements:
                requirements.append(requirement)
    manual_certificates = (manual_review or {}).get("certificate_requirements", [])
    if isinstance(manual_certificates, list):
        for certificate in manual_certificates:
            if isinstance(certificate, str) and certificate not in certificates:
                certificates.append(certificate)
    manual_evidence = (manual_review or {}).get("hard_requirement_evidence", [])
    if isinstance(manual_evidence, list):
        for excerpt in manual_evidence:
            if isinstance(excerpt, str) and excerpt not in evidence:
                evidence.append(excerpt)

    if manual_review:
        status = "manually reviewed posting images"
    elif content:
        status = "readable posting text reviewed"
    else:
        status = "unverified: no readable description text"
    return requirements, certificates, evidence, status


def work_term_has_ended(work_term_end: Any, *, as_of: date) -> bool:
    if not isinstance(work_term_end, str) or not work_term_end:
        return False
    try:
        return date.fromisoformat(work_term_end) < as_of
    except ValueError:
        return False


def exclusion_reasons(
    record: dict[str, Any],
    *,
    sat_sun_only: bool,
    exclude_stale: bool,
    exclude_driving_required: bool,
    exclude_month_or_longer: bool,
) -> list[str]:
    reasons: list[str] = []
    if sat_sun_only and not record["saturday_and_sunday_only"]:
        reasons.append(record["saturday_and_sunday_basis"])
    if exclude_stale and record["work_term_ended"]:
        reasons.append(f"work term ended on {record['work_term_end']}")
    if exclude_driving_required and record["driving_license_required"]:
        reasons.append(record["driving_license_basis"])
    if exclude_month_or_longer and record["requires_month_or_longer"]:
        reasons.append(record["duration_requirement"])
    return reasons


def parse_clock_minutes(clock_time: str | None) -> int | None:
    if not clock_time or not TIME_RE.fullmatch(clock_time):
        return None
    hours, minutes = (int(part) for part in clock_time.split(":"))
    return hours * 60 + minutes


def end_time_info(
    start_time: Any,
    end_time: Any,
    *,
    threshold: str,
) -> EndTimeInfo:
    start = start_time if isinstance(start_time, str) and TIME_RE.fullmatch(start_time) else None
    end = end_time if isinstance(end_time, str) and TIME_RE.fullmatch(end_time) else None
    start_minutes = parse_clock_minutes(start)
    end_minutes = parse_clock_minutes(end)
    threshold_minutes = parse_clock_minutes(threshold)
    if threshold_minutes is None:
        raise ValueError(f"Invalid end-time threshold: {threshold}")

    overnight = (
        end_minutes <= start_minutes
        if start_minutes is not None and end_minutes is not None
        else None
    )
    ends_before = (
        end_minutes < threshold_minutes if end_minutes is not None else None
    )
    return EndTimeInfo(start, end, overnight, ends_before)


def assess_early_weekend_shift_options(
    view_data: dict[str, Any], *, threshold: str
) -> list[str]:
    """Find advertised daytime weekend options ending before the threshold."""

    threshold_minutes = parse_clock_minutes(threshold)
    if threshold_minutes is None:
        return []
    title = str(view_data.get("recruitTitle") or "")
    note = str(view_data.get("workWeekEtc") or "")
    description = plain_text(view_data.get("content"))
    text = " ".join((title, note, description))
    colon_range = re.compile(
        r"(?<!\d)(\d{1,2})\s*:\s*(\d{2})\s*[~～-]\s*"
        r"(\d{1,2})\s*:\s*(\d{2})(?!\d)"
    )
    korean_range = re.compile(
        r"(?<!\d)(\d{1,2})시\s*(?:(\d{1,2})분)?\s*[~～-]\s*"
        r"(\d{1,2})시\s*(?:(\d{1,2})분)?"
    )
    exact_weekend, _ = assess_saturday_and_sunday_only(view_data)
    evidence: list[str] = []
    matches = sorted(
        [*colon_range.finditer(text), *korean_range.finditer(text)],
        key=lambda item: item.start(),
    )
    for match in matches:
        if "오전" in text[max(0, match.start() - 4) : match.start()] or "오후" in text[
            max(0, match.start() - 4) : match.start()
        ]:
            continue
        start = int(match.group(1)) * 60 + int(match.group(2) or 0)
        end = int(match.group(3)) * 60 + int(match.group(4) or 0)
        if start < 6 * 60 or start >= end or end >= threshold_minutes:
            continue
        context_start = max(0, match.start() - 150)
        context_end = min(len(text), match.end() + 55)
        context = text[context_start:context_end]
        before = text[context_start : match.start()]
        weekend_position = max(
            before.rfind(marker)
            for marker in (
                "주말", "토,일", "토, 일", "토/일", "토 / 일", "토-일",
                "토 - 일", "토일", "토요일",
            )
        )
        weekday_position = max(
            before.rfind(marker)
            for marker in (
                "평일", "월-금", "월 - 금", "월~금", "목,금", "목, 금", "목금",
            )
        )
        weekend_context = weekend_position >= 0 and weekend_position >= weekday_position
        if not weekend_context and not (exact_weekend and weekday_position < 0):
            continue
        excerpt = f"{match.group(0)} — " + re.sub(r"\s+", " ", context).strip()
        if excerpt not in evidence:
            evidence.append(excerpt)
    return evidence


def enum_description(value: Any) -> str:
    if isinstance(value, dict):
        description = value.get("description")
        return description if isinstance(description, str) else ""
    return str(value) if value is not None else ""


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            text = item.get("description") or item.get("name") or item.get("key")
            if isinstance(text, str) and text:
                result.append(text)
    return result


def application_methods(view_data: dict[str, Any]) -> list[str]:
    method_flags = {
        "applyOnlineStatus": "online",
        "applyEmailStatus": "email",
        "applyHomepageStatus": "homepage",
        "applySmsStatus": "sms",
        "applyEasySmsStatus": "easy_sms",
        "applyTellStatus": "phone",
        "applyVisitStatus": "visit",
        "applyEtcPossibleStatus": "other",
    }
    methods = [name for field, name in method_flags.items() if view_data.get(field)]
    if not methods:
        methods = [str(value) for value in view_data.get("applyType", [])]
    return methods


def build_record(
    search_job: dict[str, Any],
    view_data: dict[str, Any],
    *,
    end_before: str,
    manual_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recruit_no = int(view_data.get("recruitNo") or search_job["recruitNo"])
    time_info = end_time_info(
        view_data.get("workStartTime"),
        view_data.get("workEndTime"),
        threshold=end_before,
    )
    early_shift_evidence = assess_early_weekend_shift_options(
        view_data, threshold=end_before
    )
    weekend_shift_options = extract_weekend_shift_options(view_data)
    late_night_compatible, late_night_shift_options = assess_late_night_compatible(
        view_data
    )
    target_date_options, target_date_option_evidence = assess_independent_target_dates(
        view_data, (date(2026, 8, 15), date(2026, 8, 17))
    )
    salary, salary_notes, salary_detail_evidence = detail_adjusted_salary(
        search_job, view_data
    )
    weekdays = explicit_weekdays(view_data.get("workWeek"))
    weekend_only, weekend_basis = assess_saturday_and_sunday_only(view_data)
    weekend_compatible, weekend_compatible_basis = assess_weekend_compatible(view_data)
    driving_required, driving_basis = assess_driving_license_requirement(view_data)
    demographics_match, demographics_basis = demographic_compatible(
        view_data, age=26
    )
    education_match, education_basis = education_compatible(view_data)
    (
        logistics_job,
        logistics_category,
        logistics_basis,
        cold_storage_status,
        cold_storage_evidence,
    ) = assess_logistics_job(search_job, view_data)
    duration = assess_duration_requirement(view_data, manual_review)
    hard_requirements, certificates, requirement_evidence, requirement_status = (
        assess_hard_requirements(view_data, duration, manual_review)
    )

    return {
        "recruit_no": recruit_no,
        "url": f"{SITE_URL}/jobs/detail/{recruit_no}",
        "title": view_data.get("recruitTitle") or search_job.get("recruitTitle", ""),
        "company": view_data.get("recruitCompanyName")
        or search_job.get("companyName", ""),
        "area": enum_description(view_data.get("area"))
        or search_job.get("workplaceArea", ""),
        "working_address": view_data.get("workingAddress")
        or search_job.get("workplaceAddress", ""),
        "latitude": view_data.get("latitude"),
        "longitude": view_data.get("longitude"),
        "posted_at": view_data.get("registDateTime") or search_job.get("postedDate"),
        "modified_at": view_data.get("modifyDateTime"),
        "closing_at": view_data.get("closingDateTime"),
        "closing_label": view_data.get("closingDateTerm")
        or search_job.get("closingDateWithDDay", ""),
        "posting_status": enum_description(search_job.get("postStatusType")),
        "work_period": enum_description(view_data.get("workPeriod"))
        or search_job.get("workingPeriod", ""),
        "work_period_value": view_data.get("workPeriod", {}).get("value", "")
        if isinstance(view_data.get("workPeriod"), dict)
        else "",
        "structured_month_or_longer": structured_month_or_longer(
            view_data.get("workPeriod")
        ),
        "minimum_commitment_months": duration.minimum_months,
        "minimum_commitment_days": duration.minimum_days,
        "duration_hard_minimum": duration.hard_minimum,
        "duration_requirement": duration.summary,
        "duration_evidence": duration.evidence,
        "duration_source": duration.source,
        "duration_review_status": duration.status,
        "requires_month_or_longer": bool(
            duration.hard_minimum
            and duration.minimum_months is not None
            and duration.minimum_months >= 1
        ),
        "work_term": view_data.get("workTermDescription", ""),
        "work_term_start": view_data.get("workTermStartDate"),
        "work_term_end": view_data.get("workTermEndDate"),
        "work_days": enum_description(view_data.get("workDays")),
        "work_week": enum_description(view_data.get("workWeek")),
        "work_week_value": view_data.get("workWeek", {}).get("value", "")
        if isinstance(view_data.get("workWeek"), dict)
        else "",
        "weekdays": weekdays,
        "saturday_and_sunday_only": weekend_only,
        "saturday_and_sunday_basis": weekend_basis,
        "weekend_compatible": weekend_compatible,
        "weekend_compatibility_basis": weekend_compatible_basis,
        "work_time": view_data.get("workTimeDetail")
        or search_job.get("workingTime", "")
        or " / ".join(weekend_shift_options),
        "weekend_shift_options": weekend_shift_options,
        "late_night_compatible": late_night_compatible,
        "late_night_shift_options": late_night_shift_options,
        "late_night_availability": LATE_NIGHT_AVAILABILITY,
        "target_date_options": target_date_options,
        "target_date_option_evidence": target_date_option_evidence,
        "work_time_option": view_data.get("workTimeOption") or "",
        "work_time_notes": view_data.get("workTimeOpt") or "",
        "work_time_negotiable": bool(view_data.get("workTimeConsult")),
        "start_time": time_info.start_time,
        "end_time": time_info.end_time,
        "overnight": time_info.overnight,
        "ends_before_threshold": time_info.ends_before,
        "has_daytime_shift_ending_before_threshold": bool(
            (time_info.ends_before is True and not time_info.overnight)
            or early_shift_evidence
        ),
        "early_shift_evidence": early_shift_evidence,
        "end_time_threshold": end_before,
        "salary_type": enum_description(view_data.get("salaryType"))
        or enum_description(search_job.get("payType")),
        "salary": salary,
        "structured_salary": search_job.get("pay") or view_data.get("salary") or "",
        "salary_notes": salary_notes,
        "salary_detail_evidence": salary_detail_evidence,
        "employment_types": string_list(view_data.get("employmentType")),
        "parts": string_list(view_data.get("part")) or search_job.get("parts", []),
        "education": string_list(view_data.get("educationLevels")),
        "gender": enum_description(view_data.get("gender")),
        "age": view_data.get("ageDescription") or search_job.get("age", ""),
        "start_age": view_data.get("startAge") or 0,
        "end_age": view_data.get("endAge") or 0,
        "demographic_compatible": demographics_match,
        "demographic_compatibility_basis": demographics_basis,
        "education_compatible": education_match,
        "education_compatibility_basis": education_basis,
        "preferences": string_list(view_data.get("preferences")),
        "hard_requirements": hard_requirements,
        "certificate_requirements": certificates,
        "hard_requirement_evidence": requirement_evidence,
        "hard_requirement_review_status": requirement_status,
        "skills": string_list(view_data.get("skill")),
        "welfare_benefits": string_list(view_data.get("welfareBenefits")),
        "beginner_available": bool(view_data.get("beginnerAvailableStatus")),
        "friends_can_apply_together": bool(view_data.get("possibleWorkWithFriend")),
        "driving_license_required": driving_required,
        "driving_license_basis": driving_basis,
        "logistics_job": logistics_job,
        "logistics_category": logistics_category,
        "logistics_basis": logistics_basis,
        "cold_storage_status": cold_storage_status,
        "cold_storage_evidence": cold_storage_evidence,
        "is_coupang": bool(search_job.get("isCoupang")),
        "application_methods": application_methods(view_data),
        "application_possible": bool(view_data.get("applyPossibleStatue")),
        "application_status_from_public_read": enum_description(
            search_job.get("applicationStatusType")
        ),
        "applied_date_from_public_read": search_job.get("appliedDate"),
        "one_click_apply_types": string_list(view_data.get("oneClickApplyType")),
        "pre_application_questions": view_data.get("previousQuestion") or [],
        "application_risk_signals": application_risk_signals(view_data),
        "account_application_type": view_data.get("myApplyType"),
        "account_other_application_type": view_data.get("myEtcApplyType"),
        "external_recruit": bool(view_data.get("externalRecruit")),
        "external_recruit_site": view_data.get("externalRecruitSite"),
        "recruiter_name": view_data.get("recruiter"),
    }


def fetch_records(
    search_jobs: Iterable[dict[str, Any]],
    *,
    workers: int,
    end_before: str,
    manual_reviews: dict[int, dict[str, Any]] | None = None,
    detail_cache: DetailCache | None = None,
    force_refresh_ids: set[int] | None = None,
    performance_metrics: dict[str, Any] | None = None,
    persist_detail_cache: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    jobs = list(search_jobs)
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    force_refresh_ids = force_refresh_ids or set()
    details: dict[int, dict[str, Any]] = {}
    jobs_to_fetch: list[dict[str, Any]] = []
    for job in jobs:
        recruit_no = int(job["recruitNo"])
        cached = None
        if detail_cache is not None:
            cached = detail_cache.lookup(
                job,
                force_refresh=recruit_no in force_refresh_ids,
            )
        if cached is None:
            jobs_to_fetch.append(job)
        else:
            details[recruit_no] = cached

    def cache_detail(
        job: dict[str, Any], detail: dict[str, Any], fetched_via: str
    ) -> None:
        if detail_cache is not None:
            detail_cache.store(job, detail, fetched_via=fetched_via)

    fetched, fetch_errors, fetch_metrics = fetch_job_details_batch(
        jobs_to_fetch,
        workers=workers,
        on_detail=cache_detail if detail_cache is not None else None,
    )
    details.update(fetched)
    errors.extend(fetch_errors)

    for job in jobs:
        recruit_no = int(job["recruitNo"])
        view_data = details.get(recruit_no)
        if view_data is None:
            continue
        try:
            records.append(
                build_record(
                    job,
                    view_data,
                    end_before=end_before,
                    manual_review=(manual_reviews or {}).get(recruit_no),
                )
            )
        except Exception as exc:  # Keep other records, but report omissions explicitly.
            errors.append(
                {
                    "recruit_no": recruit_no,
                    "url": f"{SITE_URL}/jobs/detail/{recruit_no}",
                    "error": f"detail parsing failed: {exc}",
                }
            )
    if detail_cache is not None and persist_detail_cache:
        detail_cache.save()
    if performance_metrics is not None:
        performance_metrics.update(fetch_metrics)
        performance_metrics["detail_candidates"] = len(jobs)
        performance_metrics["detail_cache_hits"] = len(jobs) - len(jobs_to_fetch)
        performance_metrics["detail_network_jobs"] = len(jobs_to_fetch)
        if detail_cache is not None:
            performance_metrics["detail_cache"] = detail_cache.metrics()
    records.sort(key=lambda record: record["recruit_no"], reverse=True)
    errors.sort(key=lambda error: error["recruit_no"], reverse=True)
    return records, errors


def csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return str(value)


CSV_FIELDS = [
    "recruit_no",
    "title",
    "company",
    "area",
    "working_address",
    "work_term",
    "work_term_end",
    "work_term_ended",
    "work_period",
    "structured_month_or_longer",
    "minimum_commitment_months",
    "duration_hard_minimum",
    "duration_requirement",
    "duration_evidence",
    "duration_source",
    "duration_review_status",
    "requires_month_or_longer",
    "work_days",
    "work_week",
    "weekdays",
    "saturday_and_sunday_basis",
    "work_time",
    "target_date_options",
    "target_date_option_evidence",
    "start_time",
    "end_time",
    "overnight",
    "ends_before_threshold",
    "salary_type",
    "salary",
    "closing_at",
    "application_methods",
    "application_possible",
    "parts",
    "preferences",
    "hard_requirements",
    "certificate_requirements",
    "hard_requirement_evidence",
    "hard_requirement_review_status",
    "driving_license_required",
    "driving_license_basis",
    "exclusion_reasons",
    "url",
]


def write_json(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({field: csv_value(record.get(field)) for field in CSV_FIELDS})


def escape_markdown(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def before_five_label(record: dict[str, Any]) -> str:
    result = record.get("ends_before_threshold")
    if result is None:
        return "unknown"
    label = "yes" if result else "no"
    if result and record.get("overnight"):
        label += " (next day)"
    return label


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    records = report["jobs"]
    early = [record for record in records if record["ends_before_threshold"] is True]
    daytime_early = [record for record in early if not record.get("overnight")]
    overnight_early = [record for record in early if record.get("overnight")]
    unknown = [record for record in records if record["ends_before_threshold"] is None]
    lines = [
        "# Albamon Saturday-and-Sunday-only jobs",
        "",
        f"Generated: {report['generated_at']}",
        f"Saved condition: {report['suit_title']} (`{report['suit_no']}`)",
        f"Verified matches: {len(records)}",
        f"Ends before {report['end_time_threshold']} by clock time: {len(early)} "
        f"({len(daytime_early)} daytime, {len(overnight_early)} overnight)",
        f"Unknown/negotiable end time: {len(unknown)}",
        f"Excluded after eligibility checks: {report['excluded_count']}",
        "Duration exclusions use the posting description (including manually "
        "reviewed images), not Albamon's broad duration bucket.",
        "`None explicitly found` means no mandatory certificate/licence or other "
        "objective requirement was found; items labelled 우대 are preferences only.",
        "",
        f"## Day shifts ending before {report['end_time_threshold']}",
        "",
    ]
    if daytime_early:
        for record in sorted(
            daytime_early,
            key=lambda item: (item.get("end_time") or "", item["recruit_no"]),
        ):
            lines.append(
                f"- [{escape_markdown(record['title'])}]({record['url']}) — "
                f"{escape_markdown(record['company'])}; {record['work_time']}; "
                f"{record['work_week'] or record['work_days']}"
            )
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            f"## Overnight shifts ending before {report['end_time_threshold']} next day",
            "",
        ]
    )
    if overnight_early:
        for record in sorted(
            overnight_early,
            key=lambda item: (item.get("end_time") or "", item["recruit_no"]),
        ):
            lines.append(
                f"- [{escape_markdown(record['title'])}]({record['url']}) — "
                f"{escape_markdown(record['company'])}; {record['work_time']}; "
                f"{record['work_week'] or record['work_days']}"
            )
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## All verified matches",
            "",
            "| Job | Schedule | Commitment verified from post | Hard requirements | Certificate / licence | Before threshold | Apply |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for record in records:
        methods = ", ".join(record["application_methods"])
        requirements = "; ".join(record["hard_requirements"]) or "None explicitly found"
        certificates = "; ".join(record["certificate_requirements"]) or "None explicitly found"
        lines.append(
            "| "
            f"[{escape_markdown(record['title'])}]({record['url']}) | "
            f"{escape_markdown(record['work_week'] or record['work_days'])}; "
            f"{escape_markdown(record['work_time'])} | "
            f"{escape_markdown(record['duration_requirement'])} | "
            f"{escape_markdown(requirements)} | "
            f"{escape_markdown(certificates)} | "
            f"{before_five_label(record)} | "
            f"{escape_markdown(methods)} |"
        )

    lines.extend(["", "## Requirement evidence for verified matches", ""])
    for record in records:
        requirements = "; ".join(record["hard_requirements"]) or "None explicitly found"
        certificates = "; ".join(record["certificate_requirements"]) or "None explicitly found"
        evidence = " / ".join(record["hard_requirement_evidence"]) or "No mandatory requirement phrase found."
        lines.extend(
            [
                f"### [{escape_markdown(record['title'])}]({record['url']})",
                "",
                f"- Hard requirements: {escape_markdown(requirements)}",
                f"- Certificate/licence: {escape_markdown(certificates)}",
                f"- Duration: {escape_markdown(record['duration_requirement'])}",
                f"- Duration evidence: {escape_markdown(record['duration_evidence'])}",
                f"- Requirement evidence: {escape_markdown(evidence)}",
                f"- Review status: {escape_markdown(record['hard_requirement_review_status'])}",
                "",
            ]
        )

    if report["detail_errors"]:
        lines.extend(["", "## Detail pages that could not be verified", ""])
        for error in report["detail_errors"]:
            lines.append(
                f"- [{error['recruit_no']}]({error['url']}): "
                f"{escape_markdown(error['error'])}"
            )

    month_exclusions = [
        record for record in report["excluded_jobs"] if record["requires_month_or_longer"]
    ]
    if month_exclusions:
        lines.extend(["", "## Excluded: one month or longer required", ""])
        for record in month_exclusions:
            requirements = "; ".join(record["hard_requirements"]) or "None explicitly found"
            lines.append(
                f"- [{escape_markdown(record['title'])}]({record['url']}) — "
                f"{escape_markdown(record['duration_requirement'])}; "
                f"evidence: {escape_markdown(record['duration_evidence'])}; "
                f"other hard requirements: {escape_markdown(requirements)}"
            )

    other_exclusions = [
        record
        for record in report["excluded_jobs"]
        if not record["requires_month_or_longer"]
    ]
    if other_exclusions:
        lines.extend(["", "## Other exclusions", ""])
        for record in other_exclusions:
            lines.append(
                f"- [{escape_markdown(record['title'])}]({record['url']}): "
                f"{escape_markdown('; '.join(record['exclusion_reasons']))}"
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export verified jobs from an Albamon saved 맞춤알바 condition."
    )
    parser.add_argument("--suit-no", type=int, default=DEFAULT_SUIT_NO)
    parser.add_argument(
        "--strict-weekdays",
        action="store_true",
        help="Exclude jobs whose selected weekdays are negotiable.",
    )
    parser.add_argument(
        "--sat-sun-only",
        action="store_true",
        help="Keep only detail pages with exactly Saturday and Sunday selected.",
    )
    parser.add_argument(
        "--exclude-stale",
        action="store_true",
        help="Exclude jobs whose explicit work-term end date has passed.",
    )
    parser.add_argument(
        "--exclude-driving-required",
        action="store_true",
        help="Exclude explicit licence requirements and driving/valet duties.",
    )
    parser.add_argument(
        "--exclude-month-or-longer",
        action="store_true",
        help="Exclude jobs whose posting content verifies a hard one-month+ commitment.",
    )
    parser.add_argument(
        "--manual-reviews",
        type=Path,
        default=DEFAULT_MANUAL_REVIEWS_PATH,
        help="JSON evidence for descriptions that are rendered only as images.",
    )
    parser.add_argument(
        "--end-before",
        default="17:00",
        help="Annotate whether the structured shift end is before HH:MM.",
    )
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=Path("artifacts/albamon/latest"),
        help="Write .json, .csv, and .md files using this path prefix.",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.page_size < 1 or args.page_size > 10_000:
        raise ValueError("--page-size must be between 1 and 10000")
    if args.workers < 1 or args.workers > 32:
        raise ValueError("--workers must be between 1 and 32")
    if parse_clock_minutes(args.end_before) is None:
        raise ValueError("--end-before must be a 24-hour time such as 17:00")

    raw_condition = fetch_saved_condition(args.suit_no)
    manual_reviews = load_manual_reviews(args.manual_reviews)
    search_jobs, source_total = fetch_all_search_jobs(
        raw_condition,
        page_size=args.page_size,
        strict_weekdays=args.strict_weekdays,
    )
    all_records, errors = fetch_records(
        search_jobs,
        workers=args.workers,
        end_before=args.end_before,
        manual_reviews=manual_reviews,
    )
    as_of_date = datetime.now().astimezone().date()
    records: list[dict[str, Any]] = []
    excluded_records: list[dict[str, Any]] = []
    for record in all_records:
        record["work_term_ended"] = work_term_has_ended(
            record.get("work_term_end"), as_of=as_of_date
        )
        record["exclusion_reasons"] = exclusion_reasons(
            record,
            sat_sun_only=args.sat_sun_only,
            exclude_stale=args.exclude_stale,
            exclude_driving_required=args.exclude_driving_required,
            exclude_month_or_longer=args.exclude_month_or_longer,
        )
        if record["exclusion_reasons"]:
            excluded_records.append(record)
        else:
            records.append(record)

    suit = raw_condition.get("extensionCondition", {}).get("suit", {})
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {
            "saved_condition_url": (
                f"{GENERAL_API_URL}/recruit/search/condition"
                f"?recruitListType=NORMAL_SUIT&suitNo={args.suit_no}"
            ),
            "search_url": f"{GENERAL_API_URL}/recruit/search",
            "detail_url_template": f"{SITE_URL}/jobs/detail/{{recruit_no}}",
        },
        "suit_no": args.suit_no,
        "suit_title": suit.get("suitTitle") or "",
        "strict_weekdays": bool(args.strict_weekdays),
        "saturday_and_sunday_only": bool(args.sat_sun_only),
        "exclude_stale": bool(args.exclude_stale),
        "exclude_driving_required": bool(args.exclude_driving_required),
        "exclude_month_or_longer": bool(args.exclude_month_or_longer),
        "manual_reviews_path": str(args.manual_reviews),
        "manual_review_count": len(manual_reviews),
        "end_time_threshold": args.end_before,
        "source_total_count": source_total,
        "candidate_count": len(search_jobs),
        "verified_count": len(records),
        "excluded_count": len(excluded_records),
        "detail_error_count": len(errors),
        "saved_condition": raw_condition,
        "jobs": records,
        "excluded_jobs": excluded_records,
        "detail_errors": errors,
    }

    prefix = args.output_prefix
    write_json(prefix.with_suffix(".json"), report)
    write_csv(prefix.with_suffix(".csv"), records)
    write_markdown(prefix.with_suffix(".md"), report)
    return report


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        report = run(args)
    except (AlbamonError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Exported {report['verified_count']} verified jobs from "
        f"{report['candidate_count']} candidates "
        f"({report['detail_error_count']} detail errors)."
    )
    prefix = args.output_prefix
    for suffix in (".json", ".csv", ".md"):
        print(prefix.with_suffix(suffix))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
