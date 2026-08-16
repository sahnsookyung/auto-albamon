import gzip
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from tools import albamon_dashboard
from tools.albamon_dashboard import (
    _host_is_local,
    build_dashboard_payload,
    build_dashboard_snapshot,
    build_weekend_agency_payload,
    encode_response_body,
    write_disposition,
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
        self.assertEqual(1, payload["payload_version"])
        self.assertEqual({"actual": 3, "weekly_allowance": 2}, payload["summary"]["frontier_counts"])
        self.assertTrue(by_name["Far"]["pareto_actual"])
        self.assertFalse(by_name["Far"]["pareto_weekly_allowance"])
        self.assertEqual(16_800, by_name["Weekly"]["pareto_weekly_allowance_pay"])
        self.assertEqual(13_200, by_name["Near"]["pareto_weekly_allowance_pay"])
        self.assertFalse(by_name["Near"]["weekly_allowance_rate_stated"])

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

    def test_dashboard_accepts_only_loopback_host_headers(self):
        self.assertTrue(_host_is_local("127.0.0.1:8765"))
        self.assertTrue(_host_is_local("localhost:8765"))
        self.assertTrue(_host_is_local("[::1]:8765"))
        self.assertFalse(_host_is_local("jobscout.example:8765"))


if __name__ == "__main__":
    unittest.main()
