import json
import tempfile
import unittest
from pathlib import Path

from tools.albamon_application_messages import (
    PROFILE_FACTS,
    assess_application_priority,
    bmart_spec,
    build_records,
    classify_search_zone,
    classify_role,
    comparable_hourly_pay,
    commute_policy_for_job,
    fallback_regular_spec,
    load_transit_cache,
    load_message_cache,
    rerank_records,
    render_late_night_journeys,
    write_markdown,
    write_message_cache,
)


REPORT_PATH = Path(
    "artifacts/albamon/2026-08-01-combined-reranked-no-endtime-preference.json"
)


class TransitCacheOriginTests(unittest.TestCase):
    def test_future_time_journey_renders_naver_departure_and_arrival(self):
        [line] = render_late_night_journeys(
            {
                "late_night_shift_journeys": [
                    {
                        "shift": "20:00~02:00",
                        "outbound": {
                            "mode": "public_transit_future_time",
                            "minutes": 28,
                            "departure_at": "2026-08-15T19:30:00+09:00",
                            "arrival_at": "2026-08-15T19:58:00+09:00",
                        },
                        "return": {
                            "mode": "public_transit_future_time",
                            "minutes": 38,
                            "departure_at": "2026-08-16T02:00:00+09:00",
                            "arrival_at": "2026-08-16T02:38:00+09:00",
                            "vehicles": ["N31"],
                        },
                        "bicycle_fallback": {"minutes": 14, "distance_km": 3.1},
                    }
                ]
            }
        )

        self.assertIn("Naver 미래시간 28분", line)
        self.assertIn("08-16 02:00→08-16 02:38", line)
        self.assertIn("자전거 예비 14분", line)
    def test_application_report_refuses_routes_from_old_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transit.json"
            path.write_text(
                json.dumps(
                    {
                        "origin": {
                            "name": "잠실새내역",
                            "latitude": 37.5115,
                            "longitude": 127.0867,
                        },
                        "jobs": {},
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "different origin"):
                load_transit_cache(path)


class CommutePolicyTests(unittest.TestCase):
    def test_remote_job_has_no_commute_or_station_requirement(self):
        job = {
            "remote_job": True,
            "eligibility_lanes": ["remote_short_term"],
            "salary_type": "시급",
            "salary": "12,000원",
            "duration_review_status": "verified_shorter",
            "hard_requirements": [],
            "application_methods": ["online"],
            "transit_minutes": 0,
        }

        zone, label = classify_search_zone(job)
        policy = commute_policy_for_job(
            job,
            {"route_status": "not_required", "distance_excluded": False},
            hourly_pay=12_000,
        )
        _, _, reasons, _, metadata = assess_application_priority(job)

        self.assertEqual("remote", zone)
        self.assertIn("재택", label)
        self.assertFalse(policy["distance_excluded"])
        self.assertEqual(0, policy["commute_limit_minutes"])
        self.assertEqual(20, metadata["commute_priority_score"])
        self.assertIn("통근 없음", " ".join(reasons))

    def test_bangbae_address_is_classified_as_the_seocho_corridor(self):
        zone, label = classify_search_zone(
            {"working_address": "서울 서초구 방배동 123-4"}
        )

        self.assertEqual("seocho_corridor", zone)
        self.assertIn("방배", label)

    def test_standard_zones_use_an_inclusive_fifty_minute_limit(self):
        for minutes, excluded in ((49, False), (50, False), (51, True)):
            with self.subTest(minutes=minutes):
                policy = commute_policy_for_job(
                    {"area": "서울 송파구 잠실동"},
                    {"route_status": "verified", "transit_minutes": minutes},
                    hourly_pay=30_000,
                )

                self.assertEqual(50, policy["commute_limit_minutes"])
                self.assertEqual(excluded, policy["distance_excluded"])
                self.assertFalse(policy["pay_adjusted_commute"])

    def test_evening_only_lane_requires_twenty_minute_station_area(self):
        job = {
            "area": "서울 강남구 역삼동",
            "eligibility_lanes": ["songpa_late_night"],
        }
        near = commute_policy_for_job(
            job,
            {
                "route_status": "verified",
                "transit_minutes": 45,
                "latitude": 37.508844,
                "longitude": 127.063160,
            },
            hourly_pay=30_000,
        )
        far = commute_policy_for_job(
            job,
            {
                "route_status": "verified",
                "transit_minutes": 10,
                "latitude": 37.485020,
                "longitude": 126.995010,
            },
            hourly_pay=30_000,
        )
        unknown = commute_policy_for_job(
            job,
            {"route_status": "verified", "transit_minutes": 10},
            hourly_pay=30_000,
        )

        self.assertEqual(50, near["commute_limit_minutes"])
        self.assertEqual("삼성역", near["evening_nearest_station"])
        self.assertLessEqual(near["evening_station_walk_minutes"], 20)
        self.assertFalse(near["distance_excluded"])
        self.assertGreater(far["evening_station_walk_minutes"], 20)
        self.assertTrue(far["distance_excluded"])
        self.assertEqual("unverified", unknown["evening_station_area_status"])
        self.assertTrue(unknown["distance_excluded"])

    def test_weekend_job_keeps_normal_limit_when_it_also_matches_evening_lane(self):
        policy = commute_policy_for_job(
            {
                "area": "서울 송파구 잠실동",
                "eligibility_lanes": ["weekend_short_term", "songpa_late_night"],
            },
            {"route_status": "verified", "transit_minutes": 35},
            hourly_pay=12_000,
        )

        self.assertEqual(50, policy["commute_limit_minutes"])
        self.assertFalse(policy["distance_excluded"])

    def test_premium_zone_interpolates_the_pay_dependent_limit(self):
        cases = (
            (11_999, 50),
            (12_000, 50),
            (13_000, 60),
            (14_999, 79),
            (15_000, 80),
            (15_300, 81),
            (17_999, 89),
            (18_000, 90),
            (30_000, 90),
        )
        for pay, expected_limit in cases:
            with self.subTest(pay=pay):
                policy = commute_policy_for_job(
                    {"area": "서울 용산구 이태원동"},
                    {"route_status": "verified", "transit_minutes": 50},
                    hourly_pay=pay,
                )

                self.assertEqual(expected_limit, policy["commute_limit_minutes"])
                self.assertEqual(
                    expected_limit - 50,
                    policy["commute_extension_minutes"],
                )

    def test_premium_extension_is_inclusive_and_hard_capped_at_ninety(self):
        for minutes, excluded in ((89, False), (90, False), (91, True)):
            with self.subTest(minutes=minutes):
                policy = commute_policy_for_job(
                    {"area": "서울 영등포구 여의도동"},
                    {"route_status": "verified", "transit_minutes": minutes},
                    hourly_pay=18_000,
                )

                self.assertEqual(90, policy["commute_limit_minutes"])
                self.assertEqual(excluded, policy["distance_excluded"])

    def test_fifty_minute_route_is_allowed_by_the_inclusive_cutoff(self):
        policy = commute_policy_for_job(
            {"area": "서울 용산구 한남동"},
            {"route_status": "verified", "transit_minutes": 50},
            hourly_pay=30_000,
        )

        self.assertFalse(policy["distance_excluded"])
        self.assertEqual("", policy["distance_exclusion_reason"])

    def test_unverified_route_keeps_the_existing_distance_decision(self):
        policy = commute_policy_for_job(
            {"area": "서울 용산구 한남동"},
            {"route_status": "unverified", "distance_excluded": True},
            hourly_pay=15_000,
        )

        self.assertTrue(policy["distance_excluded"])
        self.assertFalse(policy["commute_policy_eligible"])
        self.assertFalse(policy["pay_adjusted_commute"])

    def test_build_records_admits_a_pay_supported_premium_stretch_job(self):
        job = {
            "report_section": "jobs",
            "utility_rank": 1,
            "utility_tier": "B",
            "utility_score": 80,
            "utility_reasons": [],
            "utility_cautions": [],
            "recruit_no": 999999980,
            "company": "프리미엄 행사장",
            "title": "주말 행사 운영",
            "url": "https://example.test/premium",
            "area": "서울 용산구 이태원동",
            "work_days": "주말(토, 일)",
            "work_time": "10:00~18:00",
            "work_time_option": "",
            "salary_type": "시급",
            "salary": "13,000원",
            "duration_requirement": "1주일",
            "availability_end_date": "2026-09-09",
            "availability_compatibility_basis": "minimum 7-day commitment fits",
            "weekend_compatibility_basis": "weekend",
            "application_methods": ["online"],
            "hard_requirements": [],
            "certificate_requirements": [],
            "parts": ["이벤트·행사스텝"],
            "skills": ["현장 운영"],
        }
        [record] = build_records(
            {"jobs": [job], "logistics_jobs": []},
            transit_cache={
                999999980: {
                    "distance_excluded": True,
                    "route_status": "verified",
                    "transit_minutes": 54,
                }
            },
        )

        self.assertEqual("apply", record["application_disposition"])
        self.assertEqual("premium_stretch", record["search_zone"])
        self.assertTrue(record["pay_adjusted_commute"])
        self.assertEqual(60, record["commute_limit_minutes"])
        self.assertIn(
            "2026-09-09",
            " ".join(record["verify_before_sending"]),
        )

    def test_songpa_late_night_lane_is_preserved_with_transport_check(self):
        job = {
            "report_section": "jobs",
            "utility_rank": 1,
            "utility_tier": "B",
            "utility_score": 80,
            "utility_reasons": [],
            "utility_cautions": [],
            "recruit_no": 999999981,
            "company": "잠실 야간매장",
            "title": "마감 스태프",
            "url": "https://example.test/night",
            "area": "서울 송파구 잠실동",
            "work_days": "주5일",
            "work_time": "20:00~02:00",
            "work_time_option": "",
            "eligibility_lanes": ["songpa_late_night"],
            "late_night_shift_options": ["20:00~02:00 — structured work time"],
            "late_night_availability": "19:00~02:00",
            "salary_type": "시급",
            "salary": "14,000원",
            "duration_requirement": "3개월 이상",
            "duration_review_status": "verified_longer",
            "weekend_compatibility_basis": "different explicit weekdays",
            "application_methods": ["online"],
            "hard_requirements": [],
            "certificate_requirements": [],
            "parts": ["매장관리"],
            "skills": [],
        }

        [record] = build_records(
            {"jobs": [job], "logistics_jobs": []},
            transit_cache={
                999999981: {
                    "route_status": "verified",
                    "transit_minutes": 12,
                    "latitude": 37.5117,
                    "longitude": 127.0862,
                    "late_night_route_checked_at": "2026-08-11T22:00:00+09:00",
                    "late_night_shift_journeys": [
                        {
                            "shift": "20:00~02:00",
                            "outbound": {"minutes": 12},
                            "return": {
                                "mode": "night_bus_pattern",
                                "minutes": 24,
                                "vehicles": ["N31(심야)"],
                            },
                            "bicycle_fallback": {
                                "minutes": 14,
                                "distance_km": 3.1,
                            },
                        }
                    ],
                    "return_bicycle_minutes": 14,
                    "return_bicycle_distance_km": 3.1,
                }
            },
        )

        self.assertEqual(["songpa_late_night"], record["eligibility_lanes"])
        self.assertIn("20:00~02:00", record["late_night_shift_options"][0])
        self.assertIn("귀가 교통수단", " ".join(record["verify_before_sending"]))
        self.assertEqual("20:00~02:00", record["late_night_shift_journeys"][0]["shift"])
        self.assertEqual(14, record["return_bicycle_minutes"])


@unittest.skipUnless(
    REPORT_PATH.exists(),
    "historical local report fixture is not checked into Git",
)
class AlbamonApplicationMessageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        cls.records = build_records(cls.report)

    def test_every_eligible_job_has_one_message(self):
        expected_ids = {
            int(job["recruit_no"])
            for job in [*self.report["jobs"], *self.report["logistics_jobs"]]
        }
        actual_ids = {record["recruit_no"] for record in self.records}

        self.assertEqual(expected_ids, actual_ids)
        self.assertEqual(41, len(self.records))

    def test_unchanged_message_analysis_is_reused_from_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "messages.json"
            write_message_cache(path, self.records)
            metrics = {}

            cached_records = build_records(
                self.report,
                message_cache=load_message_cache(path),
                message_cache_metrics=metrics,
            )

        self.assertEqual(41, metrics["message_cache_hits"])
        self.assertEqual(0, metrics["message_cache_misses"])
        self.assertEqual(
            [record["message"] for record in self.records],
            [record["message"] for record in cached_records],
        )
        self.assertEqual(
            [record["application_disposition"] for record in self.records],
            [record["application_disposition"] for record in cached_records],
        )
        self.assertEqual(
            [record["send_readiness"] for record in self.records],
            [record["send_readiness"] for record in cached_records],
        )

    def test_already_applied_jobs_are_flagged_for_separate_rendering(self):
        applied = {
            record["recruit_no"]
            for record in self.records
            if record["already_applied"]
        }

        self.assertEqual(
            {
                118002677,
                117979315,
                118044653,
                118083775,
                118115057,
                118121590,
                118150291,
                118160509,
                118171306,
                118174316,
                118197692,
                118201587,
                118206568,
                118212646,
            },
            applied,
        )
        self.assertEqual(27, sum(not record["already_applied"] for record in self.records))
        self.assertTrue(
            all(
                record["send_readiness"] == "do_not_send_already_applied"
                for record in self.records
                if record["already_applied"]
            )
        )

    def test_messages_are_job_specific_and_concise(self):
        messages = [record["message"] for record in self.records]

        self.assertEqual(len(messages), len(set(messages)))
        for message in messages:
            self.assertGreaterEqual(len(message), 100)
            self.assertLessEqual(len(message), 420)
            self.assertTrue(message.startswith("안녕하세요."))
            self.assertTrue(message.endswith("감사합니다."))
            self.assertNotIn("지원합니다", message)
            self.assertNotIn("검토 부탁드립니다", message)

    def test_sensitive_profile_fields_are_not_retained(self):
        serialized = json.dumps(PROFILE_FACTS, ensure_ascii=False).lower()

        for forbidden in ("phone", "email", "휴대폰", "정확한 주소", "resumeNo"):
            self.assertNotIn(forbidden.lower(), serialized)

    def test_confirmed_health_certificate_is_used_without_fit_penalty(self):
        by_id = {record["recruit_no"]: record for record in self.records}

        health_jobs = (
            118160509,
            118121590,
            118204044,
        )
        for recruit_no in health_jobs:
            record = by_id[recruit_no]
            self.assertIn("보건증을 보유", record["message"])
            self.assertIn("[REMOVED_PRIVATE_PROFILE_5d2d7223f1]", record["profile_facts_used"])
            self.assertFalse("보건증" in record["fit_warning"])
            self.assertTrue(any("제출 필요 시점" in note for note in record["verify_before_sending"]))

        for recruit_no in (118160509, 118121590):
            self.assertEqual(
                "do_not_send_already_applied",
                by_id[recruit_no]["send_readiness"],
            )
        self.assertEqual(
            "conditional_on_requirement", by_id[118204044]["send_readiness"]
        )

        for recruit_no in (118132532, 118199485):
            record = by_id[recruit_no]
            self.assertTrue(any("경력" in note for note in record["verify_before_sending"]))
            self.assertEqual("conditional_on_requirement", record["send_readiness"])
        self.assertEqual(
            "do_not_send_already_applied",
            by_id[118150291]["send_readiness"],
        )

    def test_bmart_requirements_are_present_on_every_branch(self):
        bmart = [
            record
            for record in self.records
            if "배민B마트" in str(record["company"])
        ]

        self.assertEqual(8, len(bmart))
        for record in bmart:
            notes = " ".join(record["verify_before_sending"])
            self.assertIn("저온", notes)
            self.assertIn("본인 명의", notes)
            self.assertEqual("conditional_on_requirement", record["send_readiness"])

    def test_known_language_mismatch_is_transparent(self):
        maison = next(
            record for record in self.records if record["recruit_no"] == 118157941
        )

        self.assertIn("중국어는 공고의 요구 수준으로 구사하지 못합니다", maison["message"])
        self.assertIn("우선순위가 매우 낮습니다", maison["fit_warning"])

    def test_future_job_gets_safe_rule_based_fallback(self):
        future = {
            "jobs": [
                {
                    "report_section": "jobs",
                    "utility_rank": 1,
                    "utility_tier": "B",
                    "utility_score": 85,
                    "recruit_no": 999999999,
                    "company": "새 카페",
                    "title": "주말 카페 고객응대",
                    "url": "https://example.test/job",
                    "area": "서울 송파구",
                    "work_days": "주말(토, 일)",
                    "work_time": "09:00~15:00",
                    "work_time_option": "",
                    "salary_type": "시급",
                    "salary": "12,000원",
                    "application_methods": ["online"],
                    "hard_requirements": ["Health certificate (보건증)"],
                    "parts": ["커피전문점"],
                    "skills": ["고객 응대", "매장 정리"],
                }
            ],
            "logistics_jobs": [],
        }

        [record] = build_records(future)

        self.assertEqual("relevance_grounded", record["message_origin"])
        self.assertIn("음식점과 [REMOVED_PRIVATE_PROFILE_7f43392d5e]", record["message"])
        self.assertIn("보건증도 보유", record["message"])
        self.assertEqual("ready_after_schedule_check", record["send_readiness"])

    def test_cleaning_message_excludes_unrelated_technical_and_language_facts(self):
        item = fallback_regular_spec(
            {
                "title": "의료기관 주말 미화스태프",
                "company": "미화업체",
                "parts": ["청소·미화"],
                "skills": [],
                "hard_requirements": [],
            }
        )

        self.assertEqual(["restaurant", "work_style"], item["facts"])
        for unrelated in ("개발", "엑셀", "코딩", "영어"):
            self.assertNotIn(unrelated, item["pitch"])
        self.assertIn("청소 구역", item["pitch"])

    def test_english_is_used_only_when_posting_signals_language_value(self):
        base = {
            "company": "행사업체",
            "parts": ["이벤트·행사스텝"],
            "skills": ["고객 안내"],
            "hard_requirements": [],
        }
        ordinary = fallback_regular_spec({**base, "title": "주말 운영스텝"})
        language = fallback_regular_spec(
            {**base, "title": "외국어 가능자 우대 고객 안내"}
        )

        self.assertNotIn("english", ordinary["facts"])
        self.assertNotIn("영어", ordinary["pitch"])
        self.assertIn("english", language["facts"])
        self.assertIn("영어", language["pitch"])

    def test_cafe_message_uses_adjacent_food_experience_without_claiming_cafe_history(self):
        item = fallback_regular_spec(
            {
                "title": "주말 카페 마감 파트",
                "company": "카페",
                "parts": ["바리스타", "커피전문점"],
                "skills": ["음료 제조", "설거지", "매장 관리"],
                "hard_requirements": [],
            }
        )

        self.assertEqual(
            ["restaurant", "hall_service", "health_certificate", "work_style"],
            item["facts"],
        )
        self.assertNotIn("computer_excel", item["facts"])
        self.assertNotIn("카페에서 근무", item["pitch"])
        self.assertIn("보건증", item["pitch"])

    def test_logistics_message_does_not_use_software_or_restaurant_history(self):
        item = bmart_spec(
            {
                "company": "배민B마트 강남점",
                "target_district": "강남구",
            }
        )

        self.assertEqual(["work_style"], item["facts"])
        self.assertNotIn("개발", item["pitch"])
        self.assertNotIn("음식점", item["pitch"])
        self.assertIn("상품 코드·수량", item["pitch"])

    def test_non_coupang_logistics_job_does_not_inherit_pickup_warning(self):
        job = {
            "report_section": "logistics",
            "logistics_category": "Other logistics/warehouse",
            "is_coupang": False,
            "utility_rank": 1,
            "utility_tier": "B",
            "utility_score": 84,
            "recruit_no": 999999989,
            "company": "매장 운영사",
            "title": "주말 매장 상품 정리",
            "url": "https://example.test/retail-stock",
            "area": "서울 강남구",
            "work_days": "주말(토, 일)",
            "work_time": "09:00~13:30",
            "work_time_option": "",
            "salary_type": "시급",
            "salary": "12,000원",
            "application_methods": ["online"],
            "hard_requirements": [],
            "certificate_requirements": [],
            "parts": ["매장관리·판매", "입출고·창고관리"],
            "skills": ["상품 정리", "재고 관리"],
        }

        [record] = build_records({"jobs": [], "logistics_jobs": [job]})

        self.assertEqual("relevance_grounded", record["message_origin"])
        self.assertNotIn("송파구 표기", record["fit_warning"])
        self.assertNotIn("실제 물류센터 주소", record["verify_before_sending"])
        self.assertEqual("ready_after_schedule_check", record["send_readiness"])

    def test_explicit_kitchen_and_gym_titles_get_correct_role_family(self):
        self.assertEqual(
            "kitchen",
            classify_role(
                {
                    "title": "애슐리 주방 주말 멤버",
                    "parts": ["서빙", "패밀리레스토랑"],
                }
            ),
        )
        self.assertEqual(
            "front_desk",
            classify_role(
                {
                    "title": "헬스장 주말 마감 스탭",
                    "parts": ["매장관리·판매", "피트니스·스포츠"],
                }
            ),
        )
        self.assertEqual(
            "kitchen",
            classify_role(
                {
                    "title": "주말 설거지 인원 채용",
                    "parts": ["서빙", "일반음식점"],
                    "skills": ["설거지"],
                }
            ),
        )

    def test_sales_crew_title_is_classified_as_retail_before_event(self):
        self.assertEqual(
            "retail",
            classify_role(
                {
                    "title": "착한구두 성수플래그쉽 행사 판매크루 채용",
                    "parts": ["이벤트·행사스텝", "매장관리·판매"],
                }
            ),
        )

    def test_motel_counter_message_focuses_on_front_desk_duties(self):
        item = fallback_regular_spec(
            {
                "title": "야간 토요일 일요일 모텔카운터에서 일하실분 구해요",
                "company": "홀리데이모텔",
                "parts": ["서비스 기타"],
                "skills": ["객실 관리", "고객 응대"],
                "hard_requirements": [],
                "pre_application_questions": [],
            }
        )

        self.assertEqual("front_desk", item["role_family"])
        self.assertEqual(
            ["카운터 고객 응대", "입·퇴실 및 예약 확인", "야간 인수인계"],
            item["employer_priorities"],
        )
        self.assertIn("야간 인수인계", item["pitch"])
        self.assertNotIn("객실 관리", item["pitch"])

    def test_wedding_expo_uses_exhibition_priorities(self):
        item = fallback_regular_spec(
            {
                "title": "코엑스 웨딩박람회 현장 안내",
                "company": "행사업체",
                "parts": ["전시·컨벤션"],
                "skills": [],
                "hard_requirements": [],
            }
        )

        self.assertIn("접수·입장 안내", item["employer_priorities"])
        self.assertNotIn("하객 맞이", item["employer_priorities"])

    def test_open_run_message_focuses_on_queue_and_early_arrival_duties(self):
        item = fallback_regular_spec(
            {
                "title": "[단기알바] 백화점 오픈런 스탭 모집",
                "company": "행사업체",
                "work_days": "주말(토, 일)",
                "work_time": "04:30~11:00",
                "parts": ["서비스 기타"],
                "skills": [],
                "hard_requirements": [],
                "pre_application_questions": [],
            }
        )

        self.assertEqual(
            ["집합시간 준수", "대기 순서·위치 준수", "현장 담당자 지시 이행"],
            item["employer_priorities"],
        )
        self.assertIn("대기 순서·위치 준수", item["pitch"])

    def test_required_suit_question_is_reported_and_keeps_send_conditional(self):
        job = {
            "report_section": "jobs",
            "utility_rank": 1,
            "utility_tier": "A",
            "utility_score": 90,
            "recruit_no": 999999990,
            "company": "행사업체",
            "title": "명품매장 입점통제 행사지원 가드",
            "url": "https://example.test/guard",
            "area": "서울 영등포구",
            "work_days": "주말(토, 일)",
            "work_time": "11:00~20:00",
            "salary_type": "일급",
            "salary": "130,000원",
            "application_methods": ["online"],
            "hard_requirements": [],
            "certificate_requirements": [],
            "parts": ["보안·경비·경호"],
            "skills": ["입출입 통제"],
            "pre_application_questions": [
                {
                    "title": "검은색 또는 다크네이비 정장 소지하고 계신가요?",
                    "required": True,
                }
            ],
        }

        [record] = build_records({"jobs": [job], "logistics_jobs": []})

        self.assertTrue(any("정장 소지" in value for value in record["hard_requirements"]))
        self.assertTrue(any("필수 질문 확인" in value for value in record["verify_before_sending"]))
        self.assertIn("정장 보유 여부", record["fit_warning"])
        self.assertEqual("conditional_on_requirement", record["send_readiness"])
        self.assertIn("정장을 보유", record["optional_sentence_if_true"])

    def test_unconfirmed_marine_licence_is_deferred(self):
        job = {
            "report_section": "jobs",
            "utility_rank": 1,
            "utility_tier": "C",
            "utility_score": 70,
            "recruit_no": 999999996,
            "company": "마리나",
            "title": "요트 운항 보조",
            "url": "https://example.test/marina",
            "area": "서울 서초구",
            "work_days": "주말(토, 일)",
            "work_time": "16:00~21:30",
            "salary_type": "시급",
            "salary": "13,000원",
            "application_methods": ["online"],
            "hard_requirements": ["Marine licence"],
            "certificate_requirements": ["Powerboat/yacht/deck-officer licence"],
            "parts": ["여행·캠프·레포츠"],
            "skills": [],
        }

        [record] = build_records({"jobs": [job], "logistics_jobs": []})

        self.assertEqual("maybe_later", record["application_disposition"])
        self.assertIn("자격증 보유 사실이 확인되지 않음", record["disposition_reason"])

    def test_cafe_experience_only_posting_is_automatically_deferred(self):
        job = {
            "report_section": "jobs",
            "utility_rank": 1,
            "utility_tier": "C",
            "utility_score": 70,
            "recruit_no": 999999997,
            "company": "카페",
            "title": "경력직만 지원요망 카페 파트너",
            "url": "https://example.test/cafe",
            "area": "서울 영등포구",
            "work_days": "주말(토, 일)",
            "work_time": "15:00~22:00",
            "salary_type": "시급",
            "salary": "12,000원",
            "application_methods": ["online"],
            "hard_requirements": [],
            "parts": ["바리스타"],
            "skills": ["음료 제조"],
        }

        [record] = build_records({"jobs": [job], "logistics_jobs": []})

        self.assertEqual("maybe_later", record["application_disposition"])
        self.assertIn("카페 실무 경력이 없음", record["disposition_reason"])

    def test_distance_excluded_job_gets_own_heading_without_application_copy(self):
        job = {
            "report_section": "jobs",
            "utility_rank": 1,
            "utility_tier": "C",
            "utility_score": 70,
            "utility_reasons": [],
            "utility_cautions": [],
            "recruit_no": 999999998,
            "company": "먼 업체",
            "title": "주말 매장관리",
            "url": "https://example.test/far",
            "area": "서울 강서구",
            "work_days": "주말(토, 일)",
            "work_time": "09:00~18:00",
            "work_time_option": "",
            "salary_type": "시급",
            "salary": "12,000원",
            "duration_requirement": "1주일",
            "weekend_compatibility_basis": "weekend",
            "application_methods": ["online"],
            "hard_requirements": [],
            "certificate_requirements": [],
            "parts": ["매장관리·판매"],
            "skills": ["상품 정리"],
        }
        [record] = build_records(
            {"jobs": [job], "logistics_jobs": []},
            transit_cache={
                999999998: {
                    "distance_excluded": True,
                    "route_status": "verified",
                    "transit_minutes": 69,
                    "distance_exclusion_reason": "잠실새내역에서 69분",
                }
            },
        )

        self.assertEqual("excluded_by_distance", record["application_disposition"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "distance.md"
            write_markdown(path, [record])
            rendered = path.read_text(encoding="utf-8")
        self.assertIn("## 거리 제외", rendered)
        self.assertIn("69분", rendered)
        self.assertNotIn("**지원 메시지**", rendered)

    def test_user_deferred_job_is_rendered_outside_actionable_sections(self):
        recruit_no = 118199485
        report = {
            "jobs": [
                job for job in self.report["jobs"] if job["recruit_no"] == recruit_no
            ],
            "logistics_jobs": [],
        }
        [record] = build_records(
            report,
            {
                recruit_no: {
                    "status": "maybe_later",
                    "reason": "현재 카페 실무 경력 없음",
                }
            },
        )

        self.assertEqual("maybe_later", record["application_disposition"])
        self.assertEqual("deferred_by_user", record["send_readiness"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unified.md"
            write_markdown(path, [record])
            rendered = path.read_text(encoding="utf-8")
        self.assertIn("## 부적합·나중에 지원 검토", rendered)
        self.assertIn("현재 카페 실무 경력 없음", rendered)

    def test_unified_markdown_contains_ranking_reasoning_and_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unified.md"
            write_markdown(path, self.records)
            rendered = path.read_text(encoding="utf-8")

        self.assertIn("# 알바몬 통합 지원 보고서", rendered)
        self.assertIn("**순위 근거**", rendered)
        self.assertIn("**핵심 조건**", rendered)
        self.assertIn("**지원 메시지**", rendered)
        first = self.records[0]
        self.assertIn(
            f"[{first['company']} — {first['title']}]({first['url']}) — "
            f"{first['salary_type']} {first['salary']}",
            rendered,
        )

    def test_ranking_weights_pay_first_and_commute_second(self):
        base = {
            "recruit_no": 999999991,
            "salary_type": "시급",
            "work_time": "09:00~18:00",
            "work_time_option": "휴게시간 60분",
            "title": "주말 알바",
            "work_days": "주말(토, 일)",
            "weekend_compatibility_basis": "weekend (Sat, Sun) detail field",
            "duration_review_status": "verified_shorter",
            "hard_requirements": [],
            "application_methods": ["online"],
        }
        high_pay_far = {**base, "salary": "14,000원", "transit_minutes": 49}
        low_pay_near = {**base, "salary": "12,500원", "transit_minutes": 14}
        high_score = assess_application_priority(high_pay_far)[0]
        low_score = assess_application_priority(low_pay_near)[0]
        self.assertGreater(high_score, low_score)

        same_pay_far = {**base, "salary": "14,000원", "transit_minutes": 49}
        same_pay_near = {**base, "salary": "14,000원", "transit_minutes": 14}
        self.assertGreater(
            assess_application_priority(same_pay_near)[0],
            assess_application_priority(same_pay_far)[0],
        )

    def test_reranking_is_strictly_pay_then_distance(self):
        base = {
            "salary_type": "시급",
            "work_time": "09:00~18:00",
            "work_time_option": "휴게시간 60분",
            "title": "주말 알바",
            "work_days": "주말(토, 일)",
            "weekend_compatibility_basis": "weekend (Sat, Sun) detail field",
            "duration_review_status": "verified_shorter",
            "hard_requirements": [],
            "application_methods": ["online"],
        }
        records = [
            {**base, "recruit_no": 1, "salary": "12,500원", "transit_minutes": 10},
            {**base, "recruit_no": 2, "salary": "14,000원", "transit_minutes": 49},
            {**base, "recruit_no": 3, "salary": "14,000원", "transit_minutes": 20},
        ]

        rerank_records(records)

        self.assertEqual([3, 2, 1], [record["recruit_no"] for record in records])

    def test_daily_pay_is_compared_using_actual_shift_hours(self):
        result = comparable_hourly_pay(
            {
                "recruit_no": 999999992,
                "salary_type": "일급",
                "salary": "100,000원",
                "title": "주말 운영스탭",
                "work_time": "10:00~18:00",
                "work_time_option": "휴게시간 60분",
            }
        )
        self.assertEqual(14_286, result["hourly_equivalent"])
        self.assertIn("7시간", result["basis"])

    def test_misleading_headline_pay_uses_detail_review_override(self):
        popup = comparable_hourly_pay(
            {
                "recruit_no": 118119949,
                "salary_type": "일급",
                "salary": "150,000원",
            }
        )
        casting = comparable_hourly_pay(
            {
                "recruit_no": 118090446,
                "salary_type": "주급",
                "salary": "1,000,000원",
            }
        )
        self.assertEqual(12_222, popup["hourly_equivalent"])
        self.assertIn("주말/", popup["basis"])
        self.assertEqual(10_320, casting["hourly_equivalent"])
        self.assertIn("회비", casting["caution"])

    def test_reviewed_weekend_pay_survives_a_repost_number_change(self):
        result = comparable_hourly_pay(
            {
                "recruit_no": 999999991,
                "company": "㈜밴스마케팅",
                "title": "[평일 1.5 / 주말 1.8] 이태원역 인근 물티슈 배포 알바 구인",
                "salary_type": "시급",
                "salary": "15,000원",
            }
        )

        self.assertEqual(18_000, result["hourly_equivalent"])
        self.assertIn("주말 시급 18,000원", result["basis"])


if __name__ == "__main__":
    unittest.main()
