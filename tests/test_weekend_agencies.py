import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest import mock

from tools import weekend_agency_refresh
from tools.weekend_agencies.config import SEARCH_DATES
from tools.weekend_agencies.http import HttpClient
from tools.weekend_agencies.hotel_digest import (
    aggregate_hotels,
    format_complete_digest,
)
from tools.weekend_agencies.models import JobOffer, PayBreakdown
from tools.weekend_agencies.notifications import (
    HotelierNotificationError,
    notify_report,
    resolve_hotelier_webhook,
)
from tools.weekend_agencies.normalize import aggregate_offers, offer_is_actionable
from tools.weekend_agencies.pay import (
    parse_eumplus_pay,
    parse_huplux_pay,
    parse_withyou_pay,
)
from tools.weekend_agencies.pareto import (
    annotate_hotel_frontiers,
    comparable_pay,
    pareto_hotel_ids,
)
from tools.weekend_agencies.pipeline import PipelineError, run_pipeline
from tools.weekend_agencies.routing import _query_for_offer
from tools.weekend_agencies.sources.eumplus import parse_page as parse_eumplus
from tools.weekend_agencies.sources.huplux import parse_page as parse_huplux
from tools.weekend_agencies.sources.withyou import parse_page as parse_withyou


WITHYOU_HTML = """
<table><tr>
<td><a href="/day_view.php?idx=101">2026-08-29</a></td>
<td><a href="/day_view.php?idx=101">JW메리어트호텔</a></td>
<td><a href="/day_view.php?idx=101">연회 서빙/10:00~19:00</a></td>
<td>13,000 <br>(15,600)</td><td>0</td><td>2명</td>
<td><input value="모집중"></td>
</tr></table>
"""

HUPLUX_HTML = """
<table><tr>
<td>서울시<br>중구</td><td>2026-08-29<br>토요일</td>
<td>[서울] 조선호텔</td><td>연회장</td>
<td>하루도 가능 <a href="/product/item.php?it_id=202"><span>주휴포함</span></a></td>
<td>성별무관<br>20세 이상</td><td><em>시급</em>13,200(주휴발생시)</td>
<td><p>09:00~18:00</p><p>14:00~23:00</p></td><td>00명</td><td><a>구인</a></td>
</tr></table>
"""

EUMPLUS_HTML = """
<table><tr onclick="location.href='./alba_view.php?Seq=303&amp;page=1'">
<td><strong>08월30일</strong> <span>일</span></td><td>JW메리어트호텔</td>
<td>[고속터미널역-주휴발생시 16,800원]라운지서빙</td>
<td>07:00~16:00</td><td>고속터미널역삼칠구</td><td>5명</td>
<td><span>주</span> <strong>14,000원</strong></td><td><strong>모집중</strong></td>
</tr></table>
"""


def offer(
    agency: str,
    source_id: str,
    pay: int,
    *,
    employer: str = "JW메리어트호텔",
    work_date: date = date(2026, 8, 29),
    start: str = "10:00",
    end: str = "19:00",
    role: str = "guest_service",
    commute: int | None = 25,
) -> JobOffer:
    return JobOffer(
        agency=agency,
        source_id=source_id,
        source_url=f"https://example.com/{source_id}",
        work_date=work_date,
        employer=employer,
        title="연회 서빙",
        role_category=role,
        shift_start=start,
        shift_end=end,
        pay=PayBreakdown(pay, int(pay * 1.2), f"{pay:,}", "test"),
        status="open",
        transit_minutes=commute,
        transit_status="verified",
    )


class PayParsingTests(unittest.TestCase):
    def test_withyou_keeps_base_and_weekly_allowance_separate(self):
        result = parse_withyou_pay("13,000 (15,600)")
        self.assertEqual(13_000, result.actual_hourly_won)
        self.assertEqual(15_600, result.weekly_allowance_hourly_won)

    def test_weekly_allowance_headline_does_not_pass_as_actual_pay(self):
        result = parse_huplux_pay("시급 13,200(주휴발생시)", "09:00", "18:00")
        self.assertEqual(11_000, result.actual_hourly_won)
        self.assertEqual(13_200, result.weekly_allowance_hourly_won)
        self.assertFalse(offer_is_actionable(replace(offer("huplux", "1", 12_000), pay=result)))

    def test_daily_pay_uses_visible_shift_duration_and_warns_about_breaks(self):
        result = parse_huplux_pay("일급 116,100원", "22:00", "06:00")
        self.assertEqual(14_513, result.actual_hourly_won)
        self.assertEqual("daily", result.pay_frequency)
        self.assertTrue(result.warnings)

    def test_eumplus_uses_listed_rate_and_validates_weekly_headline(self):
        result = parse_eumplus_pay("주 14,000원", "주휴발생시 16,800원")
        self.assertEqual(14_000, result.actual_hourly_won)
        self.assertEqual(16_800, result.weekly_allowance_hourly_won)

        conflict = parse_eumplus_pay("주 14,400원", "주휴발생시 13,200원")
        self.assertEqual(14_400, conflict.actual_hourly_won)
        self.assertIsNone(conflict.weekly_allowance_hourly_won)
        self.assertTrue(conflict.warnings)


class SourceParserTests(unittest.TestCase):
    def test_withyou_list_parser_reads_status_shift_and_pay(self):
        offers = parse_withyou(WITHYOU_HTML)
        self.assertEqual(1, len(offers))
        self.assertEqual("open", offers[0].status)
        self.assertEqual("10:00", offers[0].shift_start)
        self.assertEqual(13_000, offers[0].pay.actual_hourly_won)

    def test_huplux_parser_emits_each_selectable_shift(self):
        offers = parse_huplux(HUPLUX_HTML)
        self.assertEqual(["09:00", "14:00"], [item.shift_start for item in offers])
        self.assertEqual({11_000}, {item.pay.actual_hourly_won for item in offers})

    def test_eumplus_parser_uses_requested_date_and_login_detail_marker(self):
        offers = parse_eumplus(EUMPLUS_HTML, date(2026, 8, 30))
        self.assertEqual(1, len(offers))
        self.assertEqual(14_000, offers[0].pay.actual_hourly_won)
        self.assertEqual(16_800, offers[0].pay.weekly_allowance_hourly_won)
        self.assertTrue(offers[0].metadata["detail_requires_login"])


class AggregationTests(unittest.TestCase):
    def test_cross_agency_duplicates_keep_offers_and_name_highest_payer(self):
        groups = aggregate_offers(
            [offer("withyou", "1", 13_000), offer("eumplus", "2", 14_000)]
        )
        self.assertEqual(1, len(groups))
        self.assertTrue(groups[0]["cross_agency_duplicate"])
        self.assertEqual("eumplus", groups[0]["best_agency"])
        self.assertEqual(14_000, groups[0]["best_actual_hourly_won"])
        self.assertEqual(2, len(groups[0]["offers"]))

    def test_equal_cross_agency_pay_is_reported_as_a_tie(self):
        groups = aggregate_offers(
            [offer("withyou", "1", 13_000), offer("eumplus", "2", 13_000)]
        )

        self.assertEqual("tie", groups[0]["best_agency"])
        self.assertEqual(
            ["eumplus", "withyou"], groups[0]["highest_paying_agencies"]
        )

    def test_rank_is_actual_pay_first_then_commute(self):
        groups = aggregate_offers(
            [
                offer("withyou", "1", 13_000, employer="A", commute=10),
                offer("withyou", "2", 14_000, employer="B", commute=40),
                offer("withyou", "3", 14_000, employer="C", commute=20),
            ]
        )
        self.assertEqual(["C", "B", "A"], [item["employer"] for item in groups])

    def test_same_hotel_and_shift_does_not_merge_distinct_departments(self):
        banquet = offer("huplux", "1", 15_000)
        buffet = replace(offer("huplux", "2", 15_000), title="차르 뷔페 서빙")

        groups = aggregate_offers([banquet, buffet])

        self.assertEqual(2, len(groups))

    def test_same_agency_postings_remain_distinct_even_with_same_coarse_role(self):
        first = offer("huplux", "100:0", 15_000)
        second = replace(offer("huplux", "200:0", 15_000), title="연회 서빙 추가팀")

        groups = aggregate_offers([first, second])

        self.assertEqual(2, len(groups))
        self.assertFalse(any(item["cross_agency_duplicate"] for item in groups))

    def test_station_labels_with_exit_suffixes_use_one_station_anchor(self):
        base = offer("eumplus", "station", 14_000)
        self.assertEqual(
            "고속터미널역",
            _query_for_offer(replace(base, station="고속터미널역삼칠구")),
        )
        self.assertEqual(
            "신논현역",
            _query_for_offer(replace(base, station="신논현 4번")),
        )


class HotelParetoTests(unittest.TestCase):
    @staticmethod
    def hotel(
        hotel_id: str,
        actual: int,
        commute: int,
        weekly: int | None = None,
    ) -> dict:
        return {
            "hotel_id": hotel_id,
            "actual_hourly_max": actual,
            "weekly_allowance_hourly_max": weekly,
            "transit_minutes": commute,
        }

    def test_actual_and_weekly_allowance_frontiers_are_independent(self):
        hotels = [
            self.hotel("high-far", 15_000, 50),
            self.hotel("weekly", 14_000, 30, 16_800),
            self.hotel("near", 13_200, 18),
            self.hotel("dominated", 13_000, 40, 15_600),
        ]

        self.assertEqual(
            {"high-far", "weekly", "near"},
            pareto_hotel_ids(hotels, mode="actual"),
        )
        self.assertEqual(
            {"weekly", "near"},
            pareto_hotel_ids(hotels, mode="weekly_allowance"),
        )

    def test_weekly_mode_falls_back_to_actual_and_marks_provenance(self):
        hotels = [self.hotel("no-weekly", 15_000, 40)]

        counts = annotate_hotel_frontiers(hotels)

        self.assertEqual(15_000, comparable_pay(hotels[0], "weekly_allowance"))
        self.assertFalse(hotels[0]["weekly_allowance_rate_stated"])
        self.assertTrue(hotels[0]["pareto_actual"])
        self.assertTrue(hotels[0]["pareto_weekly_allowance"])
        self.assertEqual({"actual": 1, "weekly_allowance": 1}, counts)


class PipelineTests(unittest.TestCase):
    def test_pipeline_filters_window_and_below_threshold_and_writes_separate_files(self):
        class Source:
            name = "fixture"

            def fetch(self, _client):
                return [
                    offer("withyou", "good", 12_000),
                    offer("withyou", "low", 11_999, employer="Low"),
                    offer(
                        "withyou",
                        "weekday",
                        15_000,
                        employer="Weekday",
                        work_date=date(2026, 8, 31),
                    ),
                ]

        def no_network(items, *, api_key):
            return items, {
                "unique_location_count": 0,
                "unresolved_location_count": 0,
                "unique_route_count": 0,
                "stale_route_reuse_count": 0,
                "kakao_calls_this_run": 0,
                "kakao_quota": {},
            }

        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "latest"
            report, paths = run_pipeline(
                api_key="",
                output_prefix=prefix,
                source_cache_path=Path(directory) / "source-cache.json",
                sources=[Source()],
                route_enricher=no_network,
                now=datetime(2026, 8, 17, tzinfo=ZoneInfo("Asia/Seoul")),
            )
            self.assertEqual(1, report["counts"]["jobs"])
            self.assertTrue(report["separate_from_albamon"])
            self.assertFalse(report["policy"]["weekly_allowance_used_for_filter_or_rank"])
            self.assertTrue(Path(paths["json"]).exists())
            markdown = Path(paths["markdown"]).read_text(encoding="utf-8")
            self.assertIn("실제 시급", markdown)
            self.assertIn("주휴 적용 시급", markdown)

    def test_pipeline_preserves_previous_report_when_any_source_fails(self):
        class BrokenSource:
            name = "broken"

            def fetch(self, _client):
                raise TimeoutError("down")

        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "latest"
            previous = prefix.with_suffix(".json")
            previous.write_text('{"sentinel": true}', encoding="utf-8")
            with self.assertRaisesRegex(PipelineError, "previous complete report was preserved"):
                run_pipeline(
                    api_key="",
                    output_prefix=prefix,
                    source_cache_path=Path(directory) / "source-cache.json",
                    sources=[BrokenSource()],
                    now=datetime(2026, 8, 17, tzinfo=ZoneInfo("Asia/Seoul")),
                )
            self.assertEqual('{"sentinel": true}', previous.read_text(encoding="utf-8"))

    def test_recent_source_snapshot_degrades_visibly_and_is_bounded(self):
        class Source:
            name = "fixture"
            broken = False

            def fetch(self, _client):
                if self.broken:
                    raise TimeoutError("down")
                return [offer("withyou", "good", 12_000)]

        def no_network(items, *, api_key):
            return items, {
                "unique_location_count": 0,
                "unresolved_location_count": 0,
                "unique_route_count": 0,
                "stale_route_reuse_count": 0,
                "kakao_calls_this_run": 0,
                "kakao_quota": {},
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = Source()
            kwargs = {
                "api_key": "",
                "output_prefix": root / "latest",
                "source_cache_path": root / "source-cache.json",
                "sources": [source],
                "route_enricher": no_network,
                "now": datetime(2026, 8, 17, tzinfo=ZoneInfo("Asia/Seoul")),
            }
            first, _ = run_pipeline(**kwargs)
            source.broken = True
            second, _ = run_pipeline(
                **{**kwargs, "now": kwargs["now"] + timedelta(hours=4)}
            )

            self.assertTrue(first["all_sources_live"])
            self.assertFalse(second["all_sources_live"])
            self.assertEqual("stale_cache", second["sources"]["fixture"]["status"])
            self.assertEqual(
                kwargs["now"].isoformat(timespec="seconds"),
                second["sources"]["fixture"]["snapshot_checked_at"],
            )
            self.assertEqual(1, second["counts"]["jobs"])

            with self.assertRaisesRegex(PipelineError, "previous complete report was preserved"):
                run_pipeline(
                    **{**kwargs, "now": kwargs["now"] + timedelta(hours=9)}
                )

    def test_pipeline_stops_after_search_end(self):
        with self.assertRaisesRegex(PipelineError, "search_window_ended"):
            run_pipeline(
                api_key="",
                sources=[],
                now=datetime(2026, 9, 7, tzinfo=ZoneInfo("Asia/Seoul")),
                write_outputs=False,
            )


class HotelierNotificationTests(unittest.TestCase):
    @staticmethod
    def report_for(*offers: JobOffer) -> dict[str, object]:
        return {
            "integrity_checks_passed": True,
            "jobs": aggregate_offers(offers),
        }

    def test_resolver_uses_only_hotelier_variable(self):
        main_webhook = "https://discord.com/api/webhooks/123/albamon"
        hotelier_webhook = "https://discord.com/api/webhooks/456/hotelier"
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                f"DISCORD_WEBHOOK_URL={main_webhook}\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(
                    HotelierNotificationError, "DISCORD_WEBHOOK_URL_HOTELIER"
                ):
                    resolve_hotelier_webhook(env_file)
            env_file.write_text(
                f"DISCORD_WEBHOOK_URL={main_webhook}\n"
                f"DISCORD_WEBHOOK_URL_HOTELIER={hotelier_webhook}\n",
                encoding="utf-8",
            )
            self.assertEqual(hotelier_webhook, resolve_hotelier_webhook(env_file))

    def test_first_run_sends_every_aggregated_hotel_then_repeat_is_silent(self):
        report = self.report_for(
            offer("withyou", "1", 13_000),
            offer("withyou", "2", 14_000, employer="호텔 B"),
        )
        observed_at = datetime(2026, 8, 17, tzinfo=ZoneInfo("Asia/Seoul"))
        messages: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "notifications.json"
            first = notify_report(
                report,
                state_path=state_path,
                sender=messages.append,
                observed_at=observed_at,
            )
            second = notify_report(
                report,
                state_path=state_path,
                sender=messages.append,
                observed_at=observed_at + timedelta(hours=4),
            )

            self.assertTrue(first["bootstrapped"])
            self.assertEqual(2, first["aggregated_hotels"])
            self.assertEqual(first["sent_messages"], len(messages))
            self.assertGreaterEqual(len(messages), 2)
            rendered = "\n".join(messages)
            self.assertIn("JW메리어트 서울", rendered)
            self.assertIn("호텔 B", rendered)
            self.assertIn("주말 호텔알바 전체 요약", rendered)
            self.assertEqual(0, second["sent_messages"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(2, state["version"])
            self.assertEqual(2, len(state["hotels"]))
            self.assertTrue(state["full_digest"]["complete"])

    def test_v1_shift_state_migrates_by_sending_complete_hotel_digest(self):
        report = self.report_for(offer("withyou", "1", 13_000))
        observed_at = datetime(2026, 8, 17, tzinfo=ZoneInfo("Asia/Seoul"))
        messages: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "notifications.json"
            state_path.write_text(
                json.dumps({"version": 1, "jobs": {"old-shift": {}}}),
                encoding="utf-8",
            )
            metrics = notify_report(
                report,
                state_path=state_path,
                sender=messages.append,
                observed_at=observed_at,
            )

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(metrics["bootstrapped"])
            self.assertGreaterEqual(len(messages), 2)
            self.assertEqual(2, state["version"])
            self.assertEqual(1, state["full_digest"]["migrated_from_version"])
            self.assertTrue(state["full_digest"]["complete"])

    def test_interrupted_full_digest_resumes_after_last_checkpointed_page(self):
        report = self.report_for(
            offer("withyou", "1", 13_000),
            offer("eumplus", "2", 14_000, employer="호텔 B"),
        )
        observed_at = datetime(2026, 8, 17, tzinfo=ZoneInfo("Asia/Seoul"))
        first_messages: list[str] = []

        def interrupted_sender(message: str) -> None:
            first_messages.append(message)
            if len(first_messages) == 2:
                raise RuntimeError("simulated interruption")

        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "notifications.json"
            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                notify_report(
                    report,
                    state_path=state_path,
                    sender=interrupted_sender,
                    observed_at=observed_at,
                )
            interrupted_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(1, interrupted_state["full_digest"]["sent_pages"])
            self.assertFalse(interrupted_state["full_digest"]["complete"])

            resumed_messages: list[str] = []
            metrics = notify_report(
                report,
                state_path=state_path,
                sender=resumed_messages.append,
                observed_at=observed_at + timedelta(minutes=1),
            )

            final_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(1, metrics["resumed_from_page"])
            self.assertEqual(metrics["sent_messages"], len(resumed_messages))
            self.assertTrue(final_state["full_digest"]["complete"])

    def test_new_and_materially_changed_jobs_send_once_and_checkpoint(self):
        initial = self.report_for(offer("withyou", "1", 13_000))
        changed = self.report_for(
            offer("withyou", "1", 14_000),
            offer("eumplus", "2", 15_000, employer="호텔 B"),
        )
        observed_at = datetime(2026, 8, 17, tzinfo=ZoneInfo("Asia/Seoul"))
        messages: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "notifications.json"
            notify_report(
                initial,
                state_path=state_path,
                sender=messages.append,
                observed_at=observed_at,
            )
            messages.clear()
            metrics = notify_report(
                changed,
                state_path=state_path,
                sender=messages.append,
                observed_at=observed_at + timedelta(hours=4),
            )
            repeat = notify_report(
                changed,
                state_path=state_path,
                sender=messages.append,
                observed_at=observed_at + timedelta(hours=8),
            )

            self.assertEqual(1, metrics["new_hotels"])
            self.assertEqual(1, metrics["changed_hotels"])
            self.assertGreaterEqual(metrics["sent_messages"], 1)
            self.assertEqual(metrics["sent_messages"], len(messages))
            self.assertEqual(0, repeat["sent_messages"])

    def test_message_keeps_actual_and_weekly_allowance_pay_separate(self):
        jobs = aggregate_offers(
            [
                replace(
                    offer("eumplus", "1", 14_000),
                    pay=PayBreakdown(
                        14_000,
                        16_800,
                        "14,000 (주휴 16,800)",
                        "advertised_hourly",
                    ),
                )
            ]
        )
        report = {
            "jobs": jobs,
            "all_sources_live": True,
            "integrity_checks_passed": True,
        }
        messages = format_complete_digest(report, aggregate_hotels(jobs))
        message = "\n".join(page.message for page in messages)

        self.assertIn("실제 ₩14,000", message)
        self.assertIn("주휴* 최대 ₩16,800", message)
        self.assertTrue(all(len(page.message) <= 2_000 for page in messages))

    def test_hotel_digest_groups_rows_but_preserves_every_time_and_date(self):
        jobs = aggregate_offers(
            [
                offer("withyou", "1", 13_000, start="10:00", end="19:00"),
                offer(
                    "withyou",
                    "2",
                    13_000,
                    work_date=date(2026, 8, 30),
                    start="10:00",
                    end="19:00",
                ),
                offer("eumplus", "3", 14_000, start="13:00", end="22:00"),
            ]
        )
        hotels = aggregate_hotels(jobs)
        report = {
            "jobs": jobs,
            "all_sources_live": True,
            "integrity_checks_passed": True,
        }
        pages = format_complete_digest(report, hotels)
        overview = "\n".join(
            page.message for page in pages if "전체 요약" in page.message
        )
        details = "\n".join(
            page.message for page in pages if "업체 상세" in page.message
        )

        self.assertEqual(1, len(hotels))
        self.assertEqual(1, overview.count("JW메리어트 서울"))
        self.assertIn("`10:00~19:00`", details)
        self.assertIn("8/29,8/30", details)
        self.assertIn("`13:00~22:00`", details)
        self.assertIn("₩14,000", details)
        self.assertTrue(all(len(page.message) <= 2_000 for page in pages))

    def test_integrated_cli_uses_hotelier_notifier_when_requested(self):
        report = {
            "run_id": "run",
            "counts": {
                "jobs": 1,
                "new": 1,
                "changed": 0,
                "removed": 0,
                "cross_agency_duplicates": 0,
            },
            "routing": {
                "kakao_calls_this_run": 0,
                "unresolved_location_count": 0,
            },
            "integrity_checks_passed": True,
            "all_sources_live": True,
            "sources": {"fixture": {"status": "live"}},
            "jobs": [],
        }
        statuses: list[dict[str, object]] = []
        with (
            mock.patch.object(
                weekend_agency_refresh,
                "exclusive_file_lock",
                return_value=nullcontext(),
            ),
            mock.patch.object(
                weekend_agency_refresh,
                "run_pipeline",
                return_value=(report, {"json": "latest.json"}),
            ),
            mock.patch.object(
                weekend_agency_refresh, "read_api_key", return_value=""
            ),
            mock.patch.object(
                weekend_agency_refresh,
                "resolve_hotelier_webhook",
                return_value="https://discord.com/api/webhooks/123/hotelier",
            ) as resolver,
            mock.patch.object(
                weekend_agency_refresh,
                "notify_report",
                return_value={"sent_messages": 1},
            ) as notifier,
            mock.patch.object(
                weekend_agency_refresh,
                "emit_status",
                side_effect=lambda payload, **_kwargs: statuses.append(payload),
            ),
        ):
            result = weekend_agency_refresh.main(
                ["--env-file", "/tmp/workspace.env", "--notify"]
            )

        self.assertEqual(0, result)
        resolver.assert_called_once_with(Path("/tmp/workspace.env"))
        notifier.assert_called_once()
        self.assertEqual(
            {"sent_messages": 1}, statuses[0]["discord_notifications"]
        )


class ConfigTests(unittest.TestCase):
    def test_window_has_only_the_four_requested_weekend_dates(self):
        self.assertEqual(
            (
                date(2026, 8, 29),
                date(2026, 8, 30),
                date(2026, 9, 5),
                date(2026, 9, 6),
            ),
            SEARCH_DATES,
        )


class HttpPacingTests(unittest.TestCase):
    def test_huplux_uses_host_specific_five_second_request_cadence(self):
        class Clock:
            value = 100.0

            def monotonic(self):
                return self.value

            def sleep(self, seconds):
                self.value += seconds

        clock = Clock()
        client = HttpClient(
            minimum_interval_seconds=0.5,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )
        client._curl_get = lambda _path, _url: "ok"
        url = "https://www.huplux.com/product/list.php?start_time=2026-08-29"

        with mock.patch("tools.weekend_agencies.http.shutil.which", return_value="curl"):
            self.assertEqual("ok", client.get(url))
            clock.value += 1.0
            self.assertEqual("ok", client.get(url))

        self.assertEqual(105.0, clock.value)


if __name__ == "__main__":
    unittest.main()
