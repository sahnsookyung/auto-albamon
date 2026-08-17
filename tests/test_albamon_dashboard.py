import gzip
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import ANY, patch

from tools import albamon_dashboard
from tools.albamon_dashboard import (
    _requirement_review_fingerprint,
    _host_is_local,
    build_dashboard_payload,
    build_dashboard_snapshot,
    build_weekend_agency_payload,
    encode_response_body,
    search_weekend_agencies,
    write_disposition,
    write_review_resolution,
)
from tools.albamon_discord import notification_identity


def combined_job(recruit_no: int, **overrides) -> dict:
    job = {
        "recruit_no": recruit_no,
        "company": "테스트 식당",
        "title": "주말 홀 아르바이트",
        "area": "서울 송파구 올림픽로 1",
        "working_address": "서울 송파구 올림픽로 1",
        "work_time": "10:00~18:00",
        "latitude": 37.51,
        "longitude": 127.10,
        "target_district": "송파구",
        "closing_at": "2026-08-10T12:00:00+09:00",
        "closing_label": "D-1",
        "source_pages": ["suit"],
    }
    job.update(overrides)
    return job


def unified_job(recruit_no: int, **overrides) -> dict:
    job = {
        "recruit_no": recruit_no,
        "company": "테스트 식당",
        "title": "주말 홀 아르바이트",
        "area": "서울 송파구 올림픽로 1",
        "application_disposition": "apply",
        "send_readiness": "ready_after_schedule_check",
        "utility_rank": 1,
    }
    job.update(overrides)
    return job


class AlbamonDashboardTests(unittest.TestCase):
    def test_new_exploration_keeps_the_previous_immutable_download(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs = root / "runs"
            runs.mkdir()
            index_path = root / "index.json"
            old_id = "a" * 20
            new_id = "b" * 20
            for report_id in (old_id, new_id):
                for suffix in (".json", ".csv", ".md"):
                    (runs / f"{report_id}{suffix}").write_text(
                        f"{report_id}{suffix}\n", encoding="utf-8"
                    )
            index_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "searches": {
                            "same-search": {
                                "report_id": old_id,
                                "generated_at": "2026-08-17T10:00:00+09:00",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.multiple(
                albamon_dashboard,
                WEEKEND_AGENCY_EXPLORE_RUNS=runs,
                WEEKEND_AGENCY_EXPLORE_INDEX=index_path,
            ):
                albamon_dashboard._publish_exploration_index(
                    "same-search",
                    report_id=new_id,
                    generated_at="2026-08-17T11:00:00+09:00",
                )

            index = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(new_id, index["searches"]["same-search"]["report_id"])
            self.assertTrue((runs / f"{old_id}.json").is_file())
            self.assertTrue((runs / f"{new_id}.json").is_file())

    def test_hotel_source_cache_is_not_invalidated_by_commute_origin(self):
        dates = (date(2026, 8, 29), date(2026, 8, 30))
        first_origin = {
            "name": "First",
            "address": "서울특별시 송파구 올림픽로 1",
            "latitude": 37.51,
            "longitude": 127.1,
        }
        second_origin = {
            "name": "Second",
            "address": "서울특별시 강남구 테헤란로 1",
            "latitude": 37.5,
            "longitude": 127.03,
        }

        self.assertNotEqual(
            albamon_dashboard._exploration_signature(dates, origin=first_origin),
            albamon_dashboard._exploration_signature(dates, origin=second_origin),
        )
        self.assertEqual(
            albamon_dashboard._exploration_source_signature(dates),
            albamon_dashboard._exploration_source_signature(tuple(reversed(dates))),
        )

    def test_origin_change_is_not_published_when_hotel_rerouting_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = {
                "ALBAMON_REFRESH_LOCK": root / "albamon-refresh.lock",
                "COMBINED_REPORT": root / "combined.json",
                "ORIGIN_STATE_PATH": root / "origin.json",
                "TRANSIT_CACHE": root / "transit.json",
                "UNIFIED_REPORT": root / "unified.json",
                "UNIFIED_PREFIX": root / "unified",
                "MESSAGE_CACHE": root / "message.json",
                "WEEKEND_AGENCY_REPORT": root / "weekend.json",
                "WEEKEND_AGENCY_EXPLORE_INDEX": root / "explore-index.json",
                "WEEKEND_LOCATION_CACHE": root / "locations.json",
                "WEEKEND_ROUTE_CACHE": root / "routes.json",
                "WEEKEND_REFRESH_LOCK": root / "weekend-refresh.lock",
            }
            paths["COMBINED_REPORT"].write_text('{"jobs": []}\n', encoding="utf-8")
            paths["WEEKEND_AGENCY_REPORT"].write_text('{"jobs": []}\n', encoding="utf-8")
            paths["WEEKEND_LOCATION_CACHE"].write_text("old-locations\n", encoding="utf-8")
            paths["WEEKEND_ROUTE_CACHE"].write_text("old-routes\n", encoding="utf-8")
            previous_origin = dict(albamon_dashboard.ORIGIN)

            def fail_after_cache_side_effect(*_args, **_kwargs):
                paths["WEEKEND_LOCATION_CACHE"].write_text("partial\n", encoding="utf-8")
                paths["WEEKEND_ROUTE_CACHE"].write_text("partial\n", encoding="utf-8")
                raise RuntimeError("hotel reroute failed")

            with (
                patch.multiple(albamon_dashboard, **paths),
                patch.object(albamon_dashboard, "read_api_key", return_value="test-key"),
                patch.object(
                    albamon_dashboard,
                    "build_cache",
                    return_value={"unique_route_count": 1},
                ),
                patch.object(
                    albamon_dashboard,
                    "reroute_report",
                    side_effect=fail_after_cache_side_effect,
                ),
                patch.object(albamon_dashboard, "save_commute_origin") as save_origin,
            ):
                with self.assertRaisesRegex(RuntimeError, "hotel reroute failed"):
                    albamon_dashboard.update_commute_origin(
                        {
                            "recalculate": True,
                            "name": "Test origin",
                            "address": "서울특별시 송파구 올림픽로 1",
                            "latitude": 37.51,
                            "longitude": 127.1,
                        }
                    )

            save_origin.assert_not_called()
            self.assertEqual(previous_origin, albamon_dashboard.ORIGIN)
            self.assertFalse(paths["ORIGIN_STATE_PATH"].exists())
            self.assertEqual(
                "old-locations\n",
                paths["WEEKEND_LOCATION_CACHE"].read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "old-routes\n",
                paths["WEEKEND_ROUTE_CACHE"].read_text(encoding="utf-8"),
            )

    def test_hotel_exploration_validates_a_bounded_future_range(self):
        today = date(2026, 8, 17)

        self.assertEqual(
            (date(2026, 8, 17), date(2026, 8, 18), date(2026, 8, 19)),
            albamon_dashboard._search_dates_from_payload(
                {"start_date": "2026-08-17", "end_date": "2026-08-19"},
                today=today,
            ),
        )
        with self.assertRaisesRegex(ValueError, "earlier than today"):
            albamon_dashboard._search_dates_from_payload(
                {"start_date": "2026-08-16", "end_date": "2026-08-19"},
                today=today,
            )
        with self.assertRaisesRegex(ValueError, "at most 31 days"):
            albamon_dashboard._search_dates_from_payload(
                {"start_date": "2026-08-17", "end_date": "2026-09-17"},
                today=today,
            )

    def test_hotel_exploration_is_all_weekdays_and_never_notification_scope(self):
        report = {
            "generated_at": "2026-08-17T12:00:00+09:00",
            "policy": {
                "search_dates": ["2026-08-17", "2026-08-18"],
                "weekends_only": False,
                "notification_scope": False,
            },
            "jobs": [],
        }
        with (
            patch.object(albamon_dashboard, "read_api_key", return_value=""),
            patch.object(
                albamon_dashboard,
                "run_pipeline",
                return_value=(report, {}),
            ) as pipeline,
            patch.object(
                albamon_dashboard,
                "build_weekend_agency_payload",
                return_value={"exploration": True},
            ) as payload_builder,
        ):
            result = search_weekend_agencies(
                {
                    "start_date": "2026-08-17",
                    "end_date": "2026-08-18",
                    "force": True,
                },
                now=datetime.fromisoformat("2026-08-17T12:00:00+09:00"),
            )

        self.assertEqual({"exploration": True}, result)
        self.assertEqual(
            (date(2026, 8, 17), date(2026, 8, 18)),
            pipeline.call_args.kwargs["search_dates"],
        )
        self.assertFalse(pipeline.call_args.kwargs["weekends_only"])
        payload_builder.assert_called_once_with(
            report,
            now=datetime.fromisoformat("2026-08-17T12:00:00+09:00"),
            exploration=True,
            report_id=ANY,
        )

    def test_changed_route_origin_hides_stale_minutes_and_actionability(self):
        payload, details = build_dashboard_snapshot(
            {"jobs": [combined_job(100)], "logistics_jobs": []},
            {
                "jobs": [
                    unified_job(
                        100,
                        transit_minutes=22,
                        transit_transfers=1,
                        transit_route_status="verified",
                    )
                ]
            },
            route_origin_current=False,
        )

        self.assertFalse(payload["route_origin_current"])
        self.assertEqual(0, payload["summary"]["actionable_count"])
        self.assertIsNone(payload["jobs"][0]["transit_minutes"])
        self.assertEqual(
            "excluded_by_distance", payload["jobs"][0]["application_disposition"]
        )
        self.assertIn("재계산", details[100]["distance_exclusion_reason"])

    def test_payload_joins_map_deadline_and_notification_fields(self):
        combined = combined_job(100)
        identity = notification_identity(combined)
        state = {
            "updated_at": "2026-08-09T09:00:00+09:00",
            "jobs": {
                identity: {
                    "first_seen_at": "2026-08-09T08:30:00+09:00",
                    "notified_at": "2026-08-09T08:31:00+09:00",
                }
            },
        }

        payload = build_dashboard_payload(
            {
                "generated_at": "2026-08-09T08:00:00+09:00",
                "source_result_count": 200,
                "eligible_count": 1,
                "jobs": [combined],
                "logistics_jobs": [],
            },
            {
                "generated_at": "2026-08-09T08:05:00+09:00",
                "jobs": [unified_job(100)],
            },
            state,
            now=datetime.fromisoformat("2026-08-09T10:00:00+09:00"),
        )

        self.assertEqual(37.51, payload["jobs"][0]["latitude"])
        self.assertEqual("송파구", payload["jobs"][0]["target_district"])
        self.assertTrue(payload["jobs"][0]["is_new"])
        self.assertTrue(payload["jobs"][0]["is_closing_soon"])
        self.assertEqual(1, payload["summary"]["new_actionable_count"])
        self.assertEqual(1, payload["summary"]["closing_soon_count"])

    def test_new_badge_is_only_for_recent_actionable_jobs(self):
        actionable = combined_job(100)
        applied = combined_job(101, title="이미 지원한 공고")
        old = combined_job(102, title="오래된 공고")
        state = {
            "jobs": {
                notification_identity(actionable): {
                    "first_seen_at": "2026-08-09T09:00:00+09:00"
                },
                notification_identity(applied): {
                    "first_seen_at": "2026-08-09T09:00:00+09:00"
                },
                notification_identity(old): {
                    "first_seen_at": "2026-08-07T09:00:00+09:00"
                },
            }
        }
        payload = build_dashboard_payload(
            {"jobs": [actionable, applied, old], "logistics_jobs": []},
            {
                "jobs": [
                    unified_job(100),
                    unified_job(101, application_disposition="applied"),
                    unified_job(102),
                ]
            },
            state,
            now=datetime.fromisoformat("2026-08-09T10:00:00+09:00"),
        )

        self.assertEqual([True, False, False], [job["is_new"] for job in payload["jobs"]])

    def test_initial_payload_is_compact_and_detail_remains_complete(self):
        combined = combined_job(100, work_days="요일협의")
        unified = unified_job(
            100,
            message="지원할 때만 필요한 긴 메시지",
            utility_reasons=["verified reason"],
            weekend_compatibility_basis="negotiable schedule explicitly offers weekend work",
        )

        payload, details = build_dashboard_snapshot(
            {"jobs": [combined], "logistics_jobs": []},
            {"jobs": [unified]},
        )

        self.assertEqual(3, payload["payload_version"])
        self.assertNotIn("message", payload["jobs"][0])
        self.assertNotIn("utility_reasons", payload["jobs"][0])
        self.assertEqual("negotiable", payload["jobs"][0]["weekend_evidence_type"])
        self.assertEqual("지원할 때만 필요한 긴 메시지", details[100]["message"])
        self.assertEqual(["verified reason"], details[100]["utility_reasons"])

    def test_payload_marks_pay_commute_pareto_frontier(self):
        combined = [combined_job(recruit_no) for recruit_no in range(100, 104)]
        unified = [
            unified_job(
                100,
                comparable_hourly_pay=12_000,
                transit_minutes=10,
                commute_policy_eligible=True,
            ),
            unified_job(
                101,
                comparable_hourly_pay=15_000,
                transit_minutes=20,
                commute_policy_eligible=True,
            ),
            unified_job(
                102,
                comparable_hourly_pay=14_000,
                transit_minutes=25,
                commute_policy_eligible=True,
            ),
            unified_job(
                103,
                comparable_hourly_pay=18_000,
                transit_minutes=40,
                commute_policy_eligible=True,
            ),
        ]

        payload = build_dashboard_payload(
            {"jobs": combined, "logistics_jobs": []},
            {"jobs": unified},
        )

        frontier_ids = {
            job["recruit_no"] for job in payload["jobs"] if job["pareto_frontier"]
        }
        self.assertEqual({100, 101, 103}, frontier_ids)
        self.assertEqual(3, payload["summary"]["pareto_frontier_count"])
        self.assertEqual(
            "maximize_pay_minimize_commute", payload["pareto"]["direction"]
        )

    def test_weekend_agency_payload_exposes_both_hotel_frontiers(self):
        def agency_job(
            employer: str,
            actual: int,
            commute: int,
            weekly: int | None = None,
        ) -> dict:
            return {
                "employer": employer,
                "best_actual_hourly_won": actual,
                "best_weekly_allowance_hourly_won": weekly,
                "transit_minutes": commute,
                "work_date": "2026-08-29",
                "shift": "10:00~19:00",
                "highest_paying_agencies": ["withyou"],
                "role_category": "guest_service",
                "offers": [
                    {
                        "agency": "withyou",
                        "source_url": "https://with-you.kr/day.php",
                        "latitude": 37.51,
                        "longitude": 127.10,
                        "location_basis": "test coordinates",
                        "transit_minutes": commute,
                        "pay": {
                            "actual_hourly_won": actual,
                            "weekly_allowance_hourly_won": weekly,
                        },
                    }
                ],
            }

        payload = build_weekend_agency_payload(
            {
                "run_id": "hotel-run",
                "generated_at": "2026-08-17T12:00:00+09:00",
                "all_sources_live": True,
                "integrity_checks_passed": True,
                "counts": {"cross_agency_duplicates": 0},
                "jobs": [
                    agency_job("Far", 15_000, 50),
                    agency_job("Weekly", 14_000, 30, 16_800),
                    agency_job("Near", 13_200, 18),
                ],
            }
        )

        by_name = {hotel["employer"]: hotel for hotel in payload["hotels"]}
        self.assertEqual(2, payload["payload_version"])
        self.assertEqual({"actual": 3, "weekly_allowance": 2}, payload["summary"]["frontier_counts"])
        self.assertTrue(by_name["Far"]["pareto_actual"])
        self.assertFalse(by_name["Far"]["pareto_weekly_allowance"])
        self.assertEqual(16_800, by_name["Weekly"]["pareto_weekly_allowance_pay"])
        self.assertEqual(13_200, by_name["Near"]["pareto_weekly_allowance_pay"])
        self.assertFalse(by_name["Near"]["weekly_allowance_rate_stated"])
        self.assertEqual(37.51, by_name["Near"]["latitude"])
        self.assertEqual(127.10, by_name["Near"]["longitude"])
        self.assertEqual(albamon_dashboard.ORIGIN["name"], payload["origin"]["name"])

    def test_weekend_agency_payload_hides_expired_date_rows(self):
        def row(work_date: str, employer: str) -> dict:
            return {
                "employer": employer,
                "best_actual_hourly_won": 13_000,
                "best_weekly_allowance_hourly_won": None,
                "transit_minutes": 20,
                "work_date": work_date,
                "shift": "10:00~19:00",
                "highest_paying_agencies": ["withyou"],
                "role_category": "guest_service",
                "cross_agency_duplicate": False,
                "offers": [{
                    "agency": "withyou",
                    "source_url": "https://with-you.kr/day.php",
                    "latitude": 37.51,
                    "longitude": 127.10,
                    "transit_minutes": 20,
                    "pay": {"actual_hourly_won": 13_000},
                }],
            }

        payload = build_weekend_agency_payload(
            {"jobs": [row("2026-08-16", "Old"), row("2026-08-17", "Today")]},
            now=datetime.fromisoformat("2026-08-17T12:00:00+09:00"),
        )

        self.assertEqual(["Today"], [hotel["employer"] for hotel in payload["hotels"]])
        self.assertEqual(1, payload["summary"]["expired_job_count_hidden"])

    def test_needs_review_explains_requirements_and_deferred_jobs_only(self):
        combined = [combined_job(recruit_no) for recruit_no in range(100, 104)]
        unified = [
            unified_job(
                100,
                send_readiness="conditional_on_requirement",
                fit_warning="센터 주소 확인 필요",
                hard_requirements=["실제 센터 주소"],
            ),
            unified_job(
                101,
                application_disposition="maybe_later",
                disposition_reason="일정 재확인",
            ),
            unified_job(102, application_disposition="do_not_apply"),
            unified_job(103),
        ]

        payload = build_dashboard_payload(
            {"jobs": combined, "logistics_jobs": []},
            {"jobs": unified},
        )

        by_id = {job["recruit_no"]: job for job in payload["jobs"]}
        self.assertEqual(2, payload["summary"]["needs_review_count"])
        self.assertEqual(
            {"verify_requirements": 1, "deferred": 1},
            payload["summary"]["needs_review_breakdown"],
        )
        self.assertEqual("verify_requirements", by_id[100]["needs_review_reason"])
        self.assertEqual("deferred", by_id[101]["needs_review_reason"])
        self.assertEqual("", by_id[102]["needs_review_reason"])
        self.assertEqual("실제 센터 주소", by_id[100]["review_reason_summary"])
        self.assertEqual("일정 재확인", by_id[101]["review_reason_summary"])

    def test_matching_requirement_acknowledgement_resolves_review_until_evidence_changes(self):
        combined = [combined_job(100)]
        job = unified_job(
            100,
            send_readiness="conditional_on_requirement",
            fit_warning="보건증 확인 필요",
            hard_requirements=["보건증"],
        )
        review_state = {
            "version": 1,
            "jobs": {
                "100": {
                    "status": "requirements_verified",
                    "requirements_fingerprint": _requirement_review_fingerprint(job),
                    "resolved_at": "2026-08-17T12:00:00+09:00",
                    "note": "보유 확인",
                }
            },
        }

        resolved = build_dashboard_payload(
            {"jobs": combined, "logistics_jobs": []},
            {"jobs": [job]},
            review_state=review_state,
        )
        self.assertEqual(0, resolved["summary"]["needs_review_count"])
        self.assertEqual(1, resolved["summary"]["actionable_clear_count"])
        self.assertEqual(
            "requirements_verified", resolved["jobs"][0]["review_resolution_status"]
        )

        changed_job = {**job, "hard_requirements": ["보건증", "운전면허"]}
        reopened = build_dashboard_payload(
            {"jobs": combined, "logistics_jobs": []},
            {"jobs": [changed_job]},
            review_state=review_state,
        )
        self.assertEqual("verify_requirements", reopened["jobs"][0]["needs_review_reason"])
        self.assertEqual("", reopened["jobs"][0]["review_resolution_status"])

    def test_review_resolution_can_be_confirmed_and_reopened(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "reviews.json"
            observed_at = datetime.fromisoformat("2026-08-17T13:00:00+09:00")
            write_review_resolution(
                path,
                100,
                "confirm_requirements",
                "fingerprint-1",
                "보유 확인",
                now=observed_at,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                "requirements_verified", payload["jobs"]["100"]["status"]
            )
            self.assertEqual("보유 확인", payload["jobs"]["100"]["note"])

            write_review_resolution(
                path,
                100,
                "reopen_requirements",
                "fingerprint-1",
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("100", payload["jobs"])

    def test_songpa_late_night_lane_is_available_to_dashboard_filters(self):
        combined = combined_job(
            100,
            eligibility_lanes=["songpa_late_night"],
            late_night_shift_options=["20:00~02:00 — structured work time"],
            late_night_availability="19:00~02:00",
        )
        unified = unified_job(
            100,
            eligibility_lanes=["songpa_late_night"],
            late_night_shift_options=["20:00~02:00 — structured work time"],
            late_night_availability="19:00~02:00",
        )

        payload = build_dashboard_payload(
            {"jobs": [combined], "logistics_jobs": []},
            {"jobs": [unified]},
        )

        job = payload["jobs"][0]
        self.assertEqual("late_night", job["weekend_evidence_type"])
        self.assertEqual(["songpa_late_night"], job["eligibility_lanes"])
        self.assertIn("20:00~02:00", job["late_night_shift_options"][0])

    def test_remote_lane_is_exposed_without_map_coordinates(self):
        combined = combined_job(
            100,
            remote_job=True,
            eligibility_lanes=["remote_short_term"],
            latitude=None,
            longitude=None,
            target_district=None,
        )
        unified = unified_job(
            100,
            remote_job=True,
            eligibility_lanes=["remote_short_term"],
            search_zone="remote",
            search_zone_label="Remote · 재택",
            transit_route_status="not_required",
            transit_minutes=0,
            commute_policy_eligible=True,
        )

        payload, _details = build_dashboard_snapshot(
            {"jobs": [combined], "logistics_jobs": []},
            {"jobs": [unified]},
            route_origin_current=False,
        )

        job = payload["jobs"][0]
        self.assertTrue(job["remote_job"])
        self.assertEqual("remote", job["weekend_evidence_type"])
        self.assertEqual("remote", job["search_zone"])
        self.assertIsNone(job["latitude"])
        self.assertIsNone(job["longitude"])
        self.assertEqual("apply", job["application_disposition"])

    def test_payload_applies_the_premium_district_commute_extension(self):
        combined = combined_job(
            100,
            area="서울 용산구 이태원동",
            working_address="서울 용산구 이태원동 1",
            target_district="용산구",
        )
        unified = unified_job(
            100,
            comparable_hourly_pay=13_000,
            transit_route_status="verified",
            transit_minutes=54,
            distance_excluded=True,
            application_methods=["online"],
        )

        payload = build_dashboard_payload(
            {"jobs": [combined], "logistics_jobs": []},
            {"jobs": [unified]},
        )

        job = payload["jobs"][0]
        self.assertEqual("premium_stretch", job["search_zone"])
        self.assertEqual(60, job["commute_limit_minutes"])
        self.assertTrue(job["commute_policy_eligible"])
        self.assertTrue(job["pay_adjusted_commute"])
        self.assertEqual(1, payload["summary"]["pay_adjusted_commute_count"])

    def test_invalid_map_coordinates_do_not_stretch_dashboard_bounds(self):
        payload = build_dashboard_payload(
            {
                "jobs": [
                    combined_job(100),
                    combined_job(101, latitude=0.0, longitude=0.0),
                ],
                "logistics_jobs": [],
            },
            {"jobs": [unified_job(100), unified_job(101)]},
        )

        self.assertEqual(
            (37.51, 127.10),
            (payload["jobs"][0]["latitude"], payload["jobs"][0]["longitude"]),
        )
        self.assertEqual(
            (None, None),
            (payload["jobs"][1]["latitude"], payload["jobs"][1]["longitude"]),
        )

    def test_large_response_uses_gzip_only_when_supported(self):
        body = ("주말 알바몬 대시보드" * 400).encode("utf-8")

        compressed, encoding = encode_response_body(body, "br, gzip")
        plain, plain_encoding = encode_response_body(body, "identity")

        self.assertEqual("gzip", encoding)
        self.assertEqual(body, gzip.decompress(compressed))
        self.assertLess(len(compressed), len(body))
        self.assertEqual((body, ""), (plain, plain_encoding))

    def test_report_cache_invalidates_when_source_file_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            combined_path = root / "combined.json"
            unified_path = root / "unified.json"
            state_path = root / "state.json"
            combined_path.write_text(
                json.dumps({"jobs": [combined_job(100)], "logistics_jobs": []}),
                encoding="utf-8",
            )
            unified_path.write_text(
                json.dumps({"jobs": [unified_job(100, title="first title")]}),
                encoding="utf-8",
            )
            state_path.write_text(json.dumps({"jobs": {}}), encoding="utf-8")

            with (
                patch.object(albamon_dashboard, "COMBINED_REPORT", combined_path),
                patch.object(albamon_dashboard, "UNIFIED_REPORT", unified_path),
                patch.object(albamon_dashboard, "DISCORD_STATE", state_path),
            ):
                albamon_dashboard._invalidate_dashboard_cache()
                first = albamon_dashboard.load_dashboard_payload()
                warm = albamon_dashboard.load_dashboard_payload()
                unified_path.write_text(
                    json.dumps({"jobs": [unified_job(100, title="updated longer title")]}),
                    encoding="utf-8",
                )
                updated = albamon_dashboard.load_dashboard_payload()
                albamon_dashboard._invalidate_dashboard_cache()

        self.assertIs(first, warm)
        self.assertEqual("first title", first["jobs"][0]["title"])
        self.assertEqual("updated longer title", updated["jobs"][0]["title"])

    def test_always_open_listing_is_not_marked_closing_soon(self):
        combined = combined_job(100, closing_label="상시모집")
        payload = build_dashboard_payload(
            {"jobs": [combined], "logistics_jobs": []},
            {"jobs": [unified_job(100)]},
            now=datetime.fromisoformat("2026-08-09T10:00:00+09:00"),
        )

        self.assertFalse(payload["jobs"][0]["is_closing_soon"])

    def test_disposition_can_be_added_and_restored_without_losing_other_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dispositions.json"
            path.write_text(
                json.dumps({"99": {"status": "maybe_later", "reason": "keep me"}}),
                encoding="utf-8",
            )
            observed_at = datetime.fromisoformat("2026-08-09T10:00:00+09:00")

            write_disposition(
                path,
                100,
                "do_not_apply",
                "  schedule   conflict  ",
                allowed_recruit_nos={100},
                now=observed_at,
            )
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("keep me", saved["99"]["reason"])
            self.assertEqual("schedule conflict", saved["100"]["reason"])
            self.assertEqual("2026-08-09", saved["100"]["recorded_at"])

            write_disposition(path, 100, "apply", "", allowed_recruit_nos={100})
            restored = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("100", restored)
            self.assertIn("99", restored)

    def test_disposition_rejects_unknown_job_and_empty_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dispositions.json"
            with self.assertRaisesRegex(ValueError, "Unknown job"):
                write_disposition(
                    path,
                    100,
                    "maybe_later",
                    "reason",
                    allowed_recruit_nos={101},
                )
            with self.assertRaisesRegex(ValueError, "short reason"):
                write_disposition(
                    path,
                    101,
                    "maybe_later",
                    "",
                    allowed_recruit_nos={101},
                )

    def test_review_resolution_prunes_jobs_missing_from_current_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review_resolutions.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "jobs": {
                            "99": {
                                "status": "requirements_verified",
                                "requirements_fingerprint": "stale",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            write_review_resolution(
                path,
                100,
                "confirm_requirements",
                "current",
                allowed_recruit_nos={100},
                now=datetime.fromisoformat("2026-08-09T10:00:00+09:00"),
            )

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("99", saved["jobs"])
            self.assertEqual("current", saved["jobs"]["100"]["requirements_fingerprint"])

            with self.assertRaisesRegex(ValueError, "Unknown job"):
                write_review_resolution(
                    path,
                    101,
                    "confirm_requirements",
                    "unknown",
                    allowed_recruit_nos={100},
                )

    def test_dashboard_accepts_only_loopback_host_headers(self):
        self.assertTrue(_host_is_local("127.0.0.1:8765"))
        self.assertTrue(_host_is_local("localhost:8765"))
        self.assertTrue(_host_is_local("[::1]:8765"))
        self.assertFalse(_host_is_local("jobscout.example:8765"))


if __name__ == "__main__":
    unittest.main()
