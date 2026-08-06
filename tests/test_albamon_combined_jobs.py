import unittest
from datetime import date

from tools.albamon_combined_jobs import (
    annotate_report_with_application_history,
    apply_saved_preferences,
    assess_utility,
    deduplicate_search_jobs,
    deduplicate_semantic,
    enrich_special_requirements,
    effective_work_term_end,
    exclude_report_jobs_ending_on_or_before,
    pre_detail_exclusion_reason,
    split_closed_detail_errors,
    target_district,
    work_period_is_requested,
    work_term_ends_on_or_before,
)


class AlbamonCombinedReportTests(unittest.TestCase):
    def test_work_period_guard_rejects_silently_added_longer_bucket(self):
        self.assertTrue(work_period_is_requested({"work_period": "1개월~3개월"}))
        self.assertFalse(work_period_is_requested({"work_period": "3개월~6개월"}))

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

    def test_combined_report_excludes_terms_ending_on_cutoff_date(self):
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

        exclude_report_jobs_ending_on_or_before(report, as_of=date(2026, 8, 2))

        self.assertEqual([2], [job["recruit_no"] for job in report["jobs"]])
        self.assertEqual([1], [job["recruit_no"] for job in report["excluded_jobs"]])
        self.assertEqual(1, report["eligible_count"])

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
                "ONE_MONTH_TO_THREE_MONTH",
            },
            {item["key"] for item in result["condition"]["workPeriodTypes"]},
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

        self.assertIn("outside Seoul", pre_detail_exclusion_reason(outside))
        self.assertIn("driving", pre_detail_exclusion_reason(driving))
        self.assertIn("clinical", pre_detail_exclusion_reason(clinical))

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
