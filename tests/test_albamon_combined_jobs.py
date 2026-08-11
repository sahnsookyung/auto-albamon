import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from tools.albamon_combined_jobs import (
    annotate_report_with_application_history,
    apply_saved_preferences,
    availability_window_exclusion_reasons,
    build_late_night_condition,
    build_multi_slot_condition,
    build_remote_condition,
    build_station_safety_condition,
    assess_utility,
    deduplicate_search_jobs,
    deduplicate_semantic,
    enrich_special_requirements,
    effective_work_term_end,
    exclude_report_jobs_ending_on_or_before,
    load_special_source_cache,
    late_night_exclusion_reasons,
    merge_by_recruit_no,
    minimum_commitment_days,
    parse_args,
    pre_detail_exclusion_reason,
    prefilter_search_jobs,
    record_work_term_has_expired,
    remote_exclusion_reasons,
    source_parity_summary,
    station_safety_proximity,
    split_closed_detail_errors,
    target_district,
    temporary_single_day_exclusion_reasons,
    work_period_is_requested,
    work_term_ends_on_or_before,
    write_special_source_cache,
)


class AlbamonCombinedReportTests(unittest.TestCase):
    def test_temporary_single_day_lane_is_active_only_august_13_and_14(self):
        record = {
            "target_date_options": ["2026-08-17"],
            "target_district": "송파구",
            "work_period": "하루(1일)",
            "work_period_value": "5",
            "demographic_compatible": True,
            "education_compatible": True,
            "work_term_ended": False,
            "driving_license_required": False,
            "requires_month_or_longer": False,
            "duration_review_status": "verified_shorter",
            "clinical_study": False,
        }

        self.assertEqual(
            [],
            temporary_single_day_exclusion_reasons(
                record, as_of=date(2026, 8, 13)
            ),
        )
        self.assertEqual(
            [],
            temporary_single_day_exclusion_reasons(
                record, as_of=date(2026, 8, 14)
            ),
        )
        self.assertIn(
            "active only",
            temporary_single_day_exclusion_reasons(
                record, as_of=date(2026, 8, 15)
            )[0],
        )

    def test_late_night_lane_can_be_excluded_for_weekend_only_refresh(self):
        args = parse_args(["--exclude-late-night"])

        self.assertTrue(args.exclude_late_night)

    def test_special_source_cache_reuses_membership_for_same_saved_filter(self):
        saved = {"condition": {"areas": [{"si": "I000"}]}}
        jobs = {
            "summer": [{"recruitNo": 1}],
            "urgent": [{"recruitNo": 2}],
            "remote": [],
            "station_safety": [],
        }
        totals = {"summer": 1, "urgent": 1, "remote": 0, "station_safety": 0}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            write_special_source_cache(path, saved, jobs, totals)

            cached = load_special_source_cache(
                path, saved, max_age_hours=24
            )
            invalidated = load_special_source_cache(
                path,
                {"condition": {"areas": [{"si": "I001"}]}},
                max_age_hours=24,
            )

        self.assertEqual((jobs, totals), cached)
        self.assertIsNone(invalidated)

    def test_fresh_saved_search_row_wins_over_cached_special_source_row(self):
        merged, source_map, duplicates = merge_by_recruit_no(
            {
                "summer": [{"recruitNo": 1, "recruitTitle": "stale title"}],
                "suit": [{"recruitNo": 1, "recruitTitle": "fresh title"}],
                "urgent": [],
            }
        )

        self.assertEqual("fresh title", merged[0]["recruitTitle"])
        self.assertEqual(["suit", "summer"], source_map[1])
        self.assertEqual(1, duplicates)

    def test_remote_and_station_conditions_are_bounded(self):
        saved = {
            "condition": {
                "areas": [{"si": "I000"}],
                "workPeriodTypes": [],
                "workWeekTypes": [{"value": "sat"}],
                "workDayTypes": [],
            }
        }
        source = {"condition": {}, "extensionCondition": {}}

        remote = build_remote_condition(source, saved)
        station = build_station_safety_condition(source, saved)

        self.assertEqual([], remote["condition"]["areas"])
        self.assertTrue(remote["condition"]["includeHomeWork"])
        self.assertEqual(3, len(remote["condition"]["workPeriodTypes"]))
        self.assertEqual(
            {"역삼", "강남", "송파", "잠실", "잠실새내", "삼성"},
            {
                item["name"]
                for item in station["extensionCondition"]["subway"]["subways"]
            },
        )

    def test_source_parity_reports_exclusive_and_overlapping_discovery(self):
        summary = source_parity_summary(
            {1: ["suit"], 2: ["remote"], 3: ["remote", "urgent"]}
        )

        self.assertEqual(1, summary["multi_source_count"])
        self.assertEqual(1, summary["exclusive_counts"]["suit"])
        self.assertEqual(1, summary["exclusive_counts"]["remote"])
        self.assertEqual([2], summary["exclusive_recruit_nos"]["remote"])

    def test_work_period_guard_rejects_silently_added_longer_bucket(self):
        self.assertFalse(work_period_is_requested({"work_period": "1개월~3개월"}))
        self.assertFalse(work_period_is_requested({"work_period": "3개월~6개월"}))

    def test_availability_window_shrinks_daily_and_is_inclusive(self):
        one_week = {
            "work_period": "1주일~1개월",
            "minimum_commitment_months": None,
            "minimum_commitment_days": None,
        }

        self.assertEqual(7, minimum_commitment_days(one_week))
        self.assertEqual(
            [],
            availability_window_exclusion_reasons(
                one_week,
                as_of=date(2026, 9, 3),
                availability_end=date(2026, 9, 9),
            ),
        )
        self.assertIn(
            "minimum 7-day commitment",
            availability_window_exclusion_reasons(
                one_week,
                as_of=date(2026, 9, 4),
                availability_end=date(2026, 9, 9),
            )[0],
        )

    def test_availability_window_rejects_fixed_terms_past_deadline(self):
        reasons = availability_window_exclusion_reasons(
            {
                "work_period": "1주일이하",
                "work_term_start": "2026-09-05",
                "work_term_end": "2026-09-12",
            },
            as_of=date(2026, 8, 16),
            availability_end=date(2026, 9, 9),
        )

        self.assertIn("extends beyond 2026-09-09", reasons[0])

    def test_closed_postings_are_not_reported_as_unexpected_detail_errors(self):
        closed, unexpected = split_closed_detail_errors(
            [
                {
                    "recruit_no": 1,
                    "error": 'ForbiddenError: "code":"RCRV006"',
                },
                {
                    "recruit_no": 3,
                    "error": 'ForbiddenError: deleted posting "code":"RCRV005"',
                },
                {"recruit_no": 2, "error": "request timed out"},
            ]
        )

        self.assertEqual([1, 3], [error["recruit_no"] for error in closed])
        self.assertEqual([2], [error["recruit_no"] for error in unexpected])

    def test_combined_report_keeps_same_day_until_exact_shift_start(self):
        self.assertTrue(
            work_term_ends_on_or_before("2026-08-02", as_of=date(2026, 8, 2))
        )
        self.assertFalse(
            work_term_ends_on_or_before("2026-08-03", as_of=date(2026, 8, 2))
        )
        self.assertEqual(
            "2026-08-02",
            effective_work_term_end(
                {"title": "잠실야구장 8/1~2일 토,일 단기"},
                as_of=date(2026, 8, 2),
            ),
        )
        self.assertEqual(
            "2026-08-01",
            effective_work_term_end(
                {
                    "title": "시급15000원/ 8월 1일/토요일/연회서비스",
                    "work_period": "하루(1일)",
                },
                as_of=date(2026, 8, 2),
            ),
        )
        self.assertEqual(
            "2026-08-09",
            effective_work_term_end(
                {
                    "title": "주차 8월 8일,9일 2일 출근가능자 모집",
                    "work_period": "1주일~1개월",
                },
                as_of=date(2026, 8, 2),
            ),
        )
        self.assertEqual(
            "2026-08-15",
            effective_work_term_end(
                {
                    "title": "8월15일 탐정들의 OOOO 보조연기자 모집!! #당일알바",
                    "work_period": "1주일이하",
                },
                as_of=date(2026, 8, 17),
            ),
        )
        self.assertIsNone(
            effective_work_term_end(
                {
                    "title": "8월15일 시작 장기 스태프 모집",
                    "work_period": "1주일~1개월",
                },
                as_of=date(2026, 8, 17),
            )
        )
        self.assertEqual(
            "2026-08-16",
            effective_work_term_end(
                {
                    "title": "호텔 8/10-8/16 주방보조 단기알바",
                    "work_period": "하루(1일)",
                    "work_term_end": "2026-08-23",
                },
                as_of=date(2026, 8, 17),
            ),
        )

        base = {
            "report_section": "jobs",
            "duplicate_recruit_nos": [],
            "weekend_compatibility_basis": "weekend (Sat, Sun) detail field",
            "duration_review_status": "verified_shorter",
            "salary_type": "시급",
            "salary": "12,000원",
            "hard_requirements": [],
            "application_methods": ["online"],
            "logistics_category": "",
            "duplicate_listing_count": 1,
            "exclusion_reasons": [],
        }
        report = {
            "jobs": [
                {**base, "recruit_no": 1, "work_term_end": "2026-08-02"},
                {**base, "recruit_no": 2, "work_term_end": "2026-08-03"},
            ],
            "logistics_jobs": [],
            "excluded_jobs": [],
        }

        exclude_report_jobs_ending_on_or_before(
            report, as_of=datetime(2026, 8, 2, 12, 0)
        )

        self.assertEqual([2, 1], [job["recruit_no"] for job in report["jobs"]])
        self.assertEqual([], report["excluded_jobs"])
        self.assertEqual(2, report["eligible_count"])

        report["jobs"][1]["work_time"] = "10:00~17:00"
        exclude_report_jobs_ending_on_or_before(
            report, as_of=datetime(2026, 8, 2, 12, 0)
        )

        self.assertEqual([2], [job["recruit_no"] for job in report["jobs"]])
        self.assertEqual([], report["excluded_jobs"])
        self.assertEqual(1, report["expired_jobs_flushed_count"])

    def test_same_day_expiry_uses_last_exact_selectable_shift(self):
        record = {
            "work_term_end": "2026-08-16",
            "weekend_shift_options": ["토 10:00~14:00", "토 19:00~23:00"],
        }

        self.assertFalse(
            record_work_term_has_expired(
                record, observed_at=datetime(2026, 8, 16, 18, 59)
            )[0]
        )
        self.assertTrue(
            record_work_term_has_expired(
                record, observed_at=datetime(2026, 8, 16, 19, 0)
            )[0]
        )

    def test_authenticated_history_marks_representative_and_duplicate_ids(self):
        report = {
            "jobs": [
                {
                    "recruit_no": 200,
                    "duplicate_recruit_nos": [199],
                },
                {
                    "recruit_no": 300,
                    "duplicate_recruit_nos": [],
                },
            ],
            "logistics_jobs": [],
        }
        history = {
            "checked_at": "2026-08-02T00:00:00+09:00",
            "source": "authenticated test history",
            "history_counts": {"total": 1},
            "jobs": {
                "199": {
                    "applied_at": "2026-08-01",
                    "method": "온라인지원",
                    "read_status": "미열람",
                }
            },
        }

        annotate_report_with_application_history(report, history)

        self.assertTrue(report["jobs"][0]["already_applied"])
        self.assertEqual("199", report["jobs"][0]["applied_recruit_no"])
        self.assertFalse(report["jobs"][1]["already_applied"])
        self.assertEqual(1, report["already_applied_count"])
        self.assertEqual(1, report["not_yet_applied_count"])

    def test_authenticated_history_matches_reviewed_historical_repost(self):
        report = {
            "jobs": [
                {
                    "recruit_no": 118270862,
                    "duplicate_recruit_nos": [],
                }
            ],
            "logistics_jobs": [],
        }
        history = {
            "jobs": {
                "118158513": {
                    "applied_at": "2026-08-01",
                    "method": "online",
                }
            }
        }

        annotate_report_with_application_history(report, history)

        self.assertTrue(report["jobs"][0]["already_applied"])
        self.assertEqual("118158513", report["jobs"][0]["applied_recruit_no"])

    def test_saved_preferences_preserve_special_source_type(self):
        special = {
            "recruitListType": "NORMAL_WANTED",
            "condition": {"areas": [], "excludeNegoWorkWeek": True},
        }
        saved = {
            "condition": {
                "areas": [{"si": "I000", "gu": "I010"}],
                "excludeNegoWorkWeek": True,
                "workPeriodTypes": [
                    {"key": "ONE_DAY", "description": "하루(1일)"},
                    {"key": "LESS_THAN_ONE_WEEK", "description": "1주일이하"},
                    {"key": "ONE_WEEK_TO_ONE_MONTH", "description": "1주일~1개월"},
                    {"key": "ONE_MONTH_TO_THREE_MONTH", "description": "1개월~3개월"},
                    {"key": "THREE_MONTH_TO_SIX_MONTH", "description": "3개월~6개월"},
                ],
            }
        }

        result = apply_saved_preferences(special, saved)

        self.assertEqual("NORMAL_WANTED", result["recruitListType"])
        self.assertEqual(
            [{"si": "I000", "gu": "", "dong": "", "name": "서울 전체"}],
            result["condition"]["areas"],
        )
        self.assertFalse(result["condition"]["excludeNegoWorkWeek"])
        self.assertTrue(result["condition"]["excludeBar"])
        self.assertEqual(
            {
                "ONE_DAY",
                "LESS_THAN_ONE_WEEK",
                "ONE_WEEK_TO_ONE_MONTH",
            },
            {item["key"] for item in result["condition"]["workPeriodTypes"]},
        )

    def test_multi_slot_source_removes_server_weekday_filter_but_stays_one_day(self):
        saved = {
            "recruitListType": "NORMAL_SUIT",
            "condition": {
                "areas": [{"si": "I000", "gu": "I010"}],
                "workWeekTypes": [
                    {
                        "key": "SATURDAY_AND_SUNDAY",
                        "value": "40",
                        "description": "주말(토,일)",
                    }
                ],
                "workDayTypes": [{"key": "SATURDAY", "value": "sat"}],
                "workPeriodTypes": [],
                "excludeNegoWorkWeek": True,
            },
        }

        result = build_multi_slot_condition(saved)

        self.assertEqual([], result["condition"]["workWeekTypes"])
        self.assertEqual([], result["condition"]["workDayTypes"])
        self.assertFalse(result["condition"]["excludeNegoWorkWeek"])
        self.assertEqual(
            ["ONE_DAY"],
            [item["key"] for item in result["condition"]["workPeriodTypes"]],
        )

    def test_evening_source_covers_station_corridor_with_any_weekday(self):
        saved = {
            "recruitListType": "NORMAL_SUIT",
            "condition": {
                "areas": [{"si": "I000", "gu": "I010"}],
                "workWeekTypes": [{"key": "SATURDAY_AND_SUNDAY"}],
                "workDayTypes": [{"key": "SATURDAY"}],
                "workPeriodTypes": [{"key": "ONE_DAY"}],
                "excludeNegoWorkWeek": True,
            },
        }

        result = build_late_night_condition(saved)

        self.assertEqual(
            [
                {"si": "I000", "gu": "I010", "dong": "", "name": "강남구 전체"},
                {"si": "I000", "gu": "I150", "dong": "", "name": "서초구 전체"},
                {"si": "I000", "gu": "I180", "dong": "", "name": "송파구 전체"},
            ],
            result["condition"]["areas"],
        )
        self.assertEqual([], result["condition"]["workWeekTypes"])
        self.assertEqual([], result["condition"]["workDayTypes"])
        self.assertEqual(
            ["ONE_DAY", "LESS_THAN_ONE_WEEK", "ONE_WEEK_TO_ONE_MONTH"],
            [item["key"] for item in result["condition"]["workPeriodTypes"]],
        )
        self.assertFalse(result["condition"]["excludeNegoWorkWeek"])

    def test_late_night_lane_ignores_weekend_but_requires_short_duration(self):
        record = {
            "target_district": "송파구",
            "late_night_compatible": True,
            "demographic_compatible": True,
            "education_compatible": True,
            "work_term_ended": False,
            "work_term_end": None,
            "driving_license_required": False,
            "clinical_study": False,
            "weekend_compatible": False,
            "work_period": "1주일이하",
            "requires_month_or_longer": False,
            "duration_review_status": "not_found",
            "duration_requirement": "No one-month minimum found",
        }

        self.assertEqual([], late_night_exclusion_reasons(record))
        for district in ("강남구", "서초구", "송파구"):
            with self.subTest(district=district):
                self.assertEqual(
                    [],
                    late_night_exclusion_reasons(
                        {**record, "target_district": district}
                    ),
                )
        self.assertIn(
            "outside the Gangnam-Seocho-Songpa corridor",
            late_night_exclusion_reasons(
                {**record, "target_district": "광진구"}
            )[0],
        )
        self.assertIn(
            "no advertised shift",
            late_night_exclusion_reasons(
                {**record, "late_night_compatible": False}
            )[0],
        )
        self.assertIn(
            "one-day through one-month",
            late_night_exclusion_reasons(
                {**record, "work_period": "6개월~1년"}
            )[0],
        )
        self.assertIn(
            "Minimum 3 months",
            late_night_exclusion_reasons(
                {
                    **record,
                    "requires_month_or_longer": True,
                    "duration_requirement": "Minimum 3 months",
                    "duration_review_status": "verified",
                }
            ),
        )

    def test_ambiguous_weekend_listing_is_not_excluded_before_detail(self):
        job = {
            "recruitNo": 1,
            "recruitTitle": "주말 매장 스태프",
            "workingWeek": "요일협의",
            "workplaceAddress": "서울 송파구 올림픽로 1",
            "latitude": 37.51,
            "longitude": 127.08,
        }

        self.assertIsNone(pre_detail_exclusion_reason(job))

    def test_conclusive_outside_seoul_driving_and_clinical_jobs_prefilter(self):
        outside = {
            "recruitNo": 1,
            "recruitTitle": "상품 피킹",
            "workplaceAddress": "경기 평택시 고렴산단로 66",
            "latitude": 37.05,
            "longitude": 126.93,
        }
        driving = {"recruitNo": 2, "recruitTitle": "주말 배송 기사"}
        clinical = {"recruitNo": 3, "recruitTitle": "생동성 시험 참여자"}
        rider = {"recruitNo": 4, "recruitTitle": "잠실 야간 라이더 모집"}

        self.assertIn("outside Seoul", pre_detail_exclusion_reason(outside))
        self.assertIn("driving", pre_detail_exclusion_reason(driving))
        self.assertIn("driving", pre_detail_exclusion_reason(rider))
        self.assertIn("clinical", pre_detail_exclusion_reason(clinical))

    def test_remote_prefilter_allows_location_but_rejects_unsafe_or_low_pay(self):
        safe = {
            "recruitNo": 1,
            "recruitTitle": "재택 문서 정리",
            "workplaceAddress": "부산광역시",
            "latitude": 35.17,
            "longitude": 129.07,
            "payType": {"description": "시급"},
            "pay": "12,000원",
        }
        risky = {**safe, "recruitNo": 2, "recruitTitle": "재택 회원가입 알바"}
        low_pay = {**safe, "recruitNo": 3, "pay": "10,000원"}

        kept, excluded = prefilter_search_jobs(
            [safe, risky, low_pay],
            {1: ["remote"], 2: ["remote"], 3: ["remote"]},
        )

        self.assertEqual([1], [job["recruitNo"] for job in kept])
        self.assertEqual({2, 3}, {job["recruit_no"] for job in excluded})

    def test_station_prefilter_requires_availability_signal(self):
        daytime_weekday = {
            "recruitNo": 1,
            "recruitTitle": "게임 테스트 매장 정리",
            "workingTime": "10:00~17:00",
            "workingWeek": "주5일",
            "workingPeriod": "하루(1일)",
        }
        evening = {
            **daytime_weekday,
            "recruitNo": 2,
            "workingTime": "19:00~01:00",
        }
        sunday = {
            **daytime_weekday,
            "recruitNo": 3,
            "workingWeek": "일요일",
        }

        kept, excluded = prefilter_search_jobs(
            [daytime_weekday, evening, sunday],
            {1: ["station_safety"], 2: ["station_safety"], 3: ["station_safety"]},
        )

        self.assertEqual({2, 3}, {job["recruitNo"] for job in kept})
        self.assertEqual([1], [job["recruit_no"] for job in excluded])

    def test_station_proximity_uses_six_station_conservative_walk_limit(self):
        near = station_safety_proximity(
            {"latitude": 37.4982, "longitude": 127.0283}
        )
        unknown = station_safety_proximity({})

        self.assertTrue(near["eligible"])
        self.assertEqual("강남역", near["nearest_station"])
        self.assertFalse(unknown["eligible"])

    def test_remote_lane_requires_safe_hourly_flexible_short_term_work(self):
        record = {
            "title": "재택 문서 정리",
            "company": "테스트",
            "salary_type": "시급",
            "salary": "12,000원",
            "demographic_compatible": True,
            "education_compatible": True,
            "work_term_ended": False,
            "work_period": "1주일이하",
            "requires_month_or_longer": False,
            "duration_review_status": "verified_shorter",
            "driving_license_required": False,
            "clinical_study": False,
            "hard_requirements": [],
            "hard_requirement_review_status": "verified",
            "weekend_compatible": False,
            "late_night_compatible": False,
            "work_days": "요일협의",
            "work_time": "시간협의",
        }

        self.assertEqual(
            [],
            remote_exclusion_reasons(
                record,
                as_of=date(2026, 8, 16),
                availability_end=date(2026, 9, 9),
            ),
        )
        self.assertIn(
            "risky data",
            remote_exclusion_reasons(
                {**record, "title": "얼굴 사진 제출 재택 알바"},
                as_of=date(2026, 8, 16),
                availability_end=date(2026, 9, 9),
            )[0],
        )

    def test_pre_detail_dedup_requires_exact_operational_signature(self):
        base = {
            "recruitTitle": "주말 홀서빙",
            "companyName": "식당",
            "workplaceAddress": "서울 송파구 잠실동 1",
            "workingTime": "10:00~16:00",
            "workingPeriod": "1주일이하",
            "workingWeek": "주말(토,일)",
            "payType": {"description": "시급"},
            "pay": "15,000원",
            "parts": ["홀서빙"],
            "applicationTypes": [{"description": "온라인지원"}],
        }
        jobs = [
            {**base, "recruitNo": 10},
            {**base, "recruitNo": 11},
            {**base, "recruitNo": 12, "pay": "16,000원"},
        ]
        source_map = {10: ["suit"], 11: ["urgent"], 12: ["suit"]}

        result, removed = deduplicate_search_jobs(jobs, source_map)

        self.assertEqual(2, len(result))
        self.assertEqual(1, removed)
        representative = next(job for job in result if job["recruitNo"] == 11)
        self.assertEqual([10], representative["_pre_detail_duplicate_recruit_nos"])
        self.assertEqual(["suit", "urgent"], source_map[11])

    def test_coupang_location_variants_are_semantically_deduplicated(self):
        base = {
            "title": "쿠팡 단순소분 원하는날",
            "company": "쿠팡",
            "work_time": "시간협의",
            "source_pages": ["urgent"],
            "is_coupang": True,
            "url": "https://example.test/1",
            "area": "서울 강남구",
        }
        first = {**base, "recruit_no": 2, "working_address": "강남구"}
        second = {
            **base,
            "recruit_no": 1,
            "working_address": "송파구",
            "url": "https://example.test/2",
            "area": "서울 송파구",
        }

        records, removed = deduplicate_semantic([first, second])

        self.assertEqual(1, removed)
        self.assertEqual([1], records[0]["duplicate_recruit_nos"])
        self.assertEqual(["서울 강남구", "서울 송파구"], records[0]["listed_areas"])

    def test_coupang_dedupe_preserves_schedule_and_guaranteed_pay_variants(self):
        base = {
            "title": "쿠팡 단순소분 원하는날",
            "company": "쿠팡",
            "work_period": "하루(1일)",
            "work_days": "요일협의",
            "salary_type": "일급",
            "source_pages": ["urgent"],
            "is_coupang": True,
            "url": "https://example.test/1",
            "area": "서울 송파구",
        }
        morning = {
            **base,
            "recruit_no": 1,
            "work_time": "09:00~18:00",
            "salary": "110,000원",
        }
        evening = {
            **base,
            "recruit_no": 2,
            "work_time": "19:00~02:00",
            "salary": "110,000원",
        }
        higher_guarantee = {
            **base,
            "recruit_no": 3,
            "work_time": "09:00~18:00",
            "salary": "130,000원",
        }

        records, removed = deduplicate_semantic(
            [morning, evening, higher_guarantee]
        )

        self.assertEqual(0, removed)
        self.assertEqual(3, len(records))

    def test_semantic_dedupe_keeps_material_schedule_and_pay_variants(self):
        base = {
            "title": "주말 행사 스태프",
            "company": "테스트 행사",
            "working_address": "서울 송파구 올림픽로 1",
            "work_time": "10:00~18:00",
            "salary_type": "시급",
            "source_pages": ["suit"],
            "url": "https://example.test/1",
            "area": "서울 송파구",
        }
        saturday = {
            **base,
            "recruit_no": 1,
            "work_days": "토요일",
            "salary": "13,000원",
        }
        sunday = {
            **base,
            "recruit_no": 2,
            "work_days": "일요일",
            "salary": "13,000원",
        }
        higher_pay = {
            **base,
            "recruit_no": 3,
            "work_days": "토요일",
            "salary": "15,000원",
        }

        records, removed = deduplicate_semantic([saturday, sunday, higher_pay])

        self.assertEqual(0, removed)
        self.assertEqual(3, len(records))

    def test_seoul_district_in_title_overrides_office_address(self):
        record = {
            "title": "양천구 물놀이장 운영요원",
            "area": "서울 강남구",
            "working_address": "서울 강남구 사무실",
            "logistics_job": False,
        }

        self.assertEqual("양천구", target_district(record))
        self.assertEqual(
            "posting title identifies work in 양천구",
            record["area_screen_note"],
        )

    def test_logistics_branch_in_another_seoul_district_is_accepted(self):
        record = {
            "title": "배민B마트 광진자양점 일일크루",
            "area": "서울 송파구",
            "working_address": "서울 송파구 본사",
            "logistics_job": True,
            "logistics_category": "Baemin B-Mart micro-fulfilment",
        }

        self.assertEqual("광진구", target_district(record))

    def test_precise_address_beats_bare_district_stem_in_store_name(self):
        record = {
            "title": "신세계강남 스위트파크 단기알바",
            "area": "서울 서초구 신반포로 176",
            "working_address": "서울 서초구 신반포로 176",
            "logistics_job": False,
        }

        self.assertEqual("서초구", target_district(record))
        self.assertEqual("detail address is in 서초구", record["area_screen_note"])

    def test_reposted_patgodang_shift_deduplicates_despite_title_suffix(self):
        base = {
            "company": "팥고당정미소 본점(파스텔시티)",
            "working_address": "서울 서초구 방배천로 11",
            "area": "서울 서초구 방배천로 11",
            "work_time": "시간협의",
            "salary": "12,000원",
            "source_pages": ["suit"],
        }
        old = {
            **base,
            "recruit_no": 118206568,
            "title": "팥고당 정미소 사당점 주말 파트타이머 모집",
            "url": "https://www.albamon.com/jobs/detail/118206568",
        }
        new = {
            **base,
            "recruit_no": 118233279,
            "title": "팥고당 정미소 사당점 주말 오픈/마감 파트타이머 모집",
            "url": "https://www.albamon.com/jobs/detail/118233279",
        }

        records, removed = deduplicate_semantic([old, new])

        self.assertEqual(1, removed)
        self.assertEqual(118233279, records[0]["recruit_no"])
        self.assertEqual([118206568], records[0]["duplicate_recruit_nos"])

    def test_logistics_address_beats_marketing_branch_label(self):
        record = {
            "title": "배민B마트 광진자양점 일일크루",
            "area": "서울 성동구 아차산로 17길",
            "working_address": "서울 성동구 아차산로 17길",
            "logistics_job": True,
            "logistics_category": "Baemin B-Mart micro-fulfilment",
        }

        self.assertEqual("성동구", target_district(record))

    def test_incheon_jung_gu_is_not_mistaken_for_seoul_jung_gu(self):
        record = {
            "title": "쿠팡 인천 신선센터",
            "area": "인천광역시 중구 서해대로",
            "working_address": "인천광역시 중구 서해대로",
            "logistics_job": True,
            "logistics_category": "Coupang Logistics Services",
        }

        self.assertIsNone(target_district(record))

    def test_ambiguous_jung_gu_with_incheon_coordinates_is_excluded(self):
        record = {
            "title": "주말 식기세척 작업",
            "area": "중구 공항동로 2길 103",
            "working_address": "중구 공항동로 2길 103",
            "latitude": 37.484676,
            "longitude": 126.451897,
            "logistics_job": False,
        }

        self.assertIsNone(target_district(record))
        self.assertIn("coordinates are outside Seoul", record["area_screen_note"])

    def test_bmart_template_adds_cold_work_requirements(self):
        record = {
            "title": "배민B마트 일일크루",
            "parts": ["물류피킹·포장·전산"],
            "logistics_category": "Baemin B-Mart micro-fulfilment",
            "hard_requirements": [],
        }

        enrich_special_requirements(record)

        self.assertIn("At least age 19", record["hard_requirements"])
        self.assertTrue(
            any("refrigerated/frozen" in item for item in record["hard_requirements"])
        )

    def test_clinical_ads_deduplicate_across_area_variants(self):
        base = {
            "title": "[단기/주말/방학] 건강기능식품 테스트",
            "company": "에이스인포",
            "work_time": "시간협의",
            "source_pages": ["summer"],
            "is_coupang": False,
            "clinical_study": True,
            "url": "https://example.test/1",
            "area": "서울 강남구",
        }
        first = {**base, "recruit_no": 2, "working_address": "강남구"}
        second = {
            **base,
            "recruit_no": 1,
            "working_address": "송파구",
            "url": "https://example.test/2",
            "area": "서울 송파구",
        }

        records, removed = deduplicate_semantic([first, second])

        self.assertEqual(1, removed)
        self.assertEqual(2, records[0]["duplicate_listing_count"])

    def test_utility_treats_shift_end_time_as_neutral(self):
        base = {
            "end_time_threshold": "17:00",
            "weekend_compatibility_basis": "weekend (Sat, Sun) detail field",
            "duration_review_status": "verified_shorter",
            "salary_type": "시급",
            "salary": "12,000원",
            "hard_requirements": [],
            "application_methods": ["online"],
            "logistics_category": "",
            "duplicate_listing_count": 1,
        }
        early = {
            **base,
            "has_daytime_shift_ending_before_threshold": True,
            "overnight": False,
            "end_time": "16:30",
        }
        overnight = {
            **base,
            "has_daytime_shift_ending_before_threshold": False,
            "overnight": True,
            "end_time": "02:00",
        }

        early_score, _, _, _ = assess_utility(early)
        overnight_score, _, _, cautions = assess_utility(overnight)

        self.assertEqual(early_score, overnight_score)
        self.assertFalse(any("overnight" in item for item in cautions))

    def test_utility_penalizes_distance_from_gangnam_and_songpa(self):
        base = {
            "weekend_compatibility_basis": "weekend (Sat, Sun) detail field",
            "duration_review_status": "verified_shorter",
            "salary_type": "시급",
            "salary": "12,000원",
            "hard_requirements": [],
            "application_methods": ["online"],
            "logistics_category": "",
            "duplicate_listing_count": 1,
        }
        gangnam = {**base, "target_district": "강남구"}
        dobong = {**base, "target_district": "도봉구"}

        gangnam_score, _, _, _ = assess_utility(gangnam)
        dobong_score, _, _, cautions = assess_utility(dobong)

        self.assertEqual(14, gangnam_score - dobong_score)
        self.assertTrue(any("도봉구" in item for item in cautions))

    def test_utility_penalizes_unmet_skill_requirement(self):
        base = {
            "end_time_threshold": "17:00",
            "has_daytime_shift_ending_before_threshold": False,
            "overnight": False,
            "end_time": "19:00",
            "weekend_compatibility_basis": "weekend (Sat, Sun) detail field",
            "duration_review_status": "not_found",
            "salary_type": "시급",
            "salary": "12,000원",
            "application_methods": ["online"],
            "logistics_category": "",
            "duplicate_listing_count": 1,
        }
        unrestricted = {**base, "hard_requirements": []}
        chinese_required = {**base, "hard_requirements": ["Chinese proficiency"]}

        unrestricted_score = assess_utility(unrestricted)[0]
        restricted_score, _, _, cautions = assess_utility(chinese_required)

        self.assertGreater(unrestricted_score, restricted_score)
        self.assertTrue(any("language" in item for item in cautions))


if __name__ == "__main__":
    unittest.main()
