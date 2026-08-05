import json
import tempfile
import unittest
from pathlib import Path

from tools.albamon_application_messages import (
    PROFILE_FACTS,
    assess_application_priority,
    bmart_spec,
    build_records,
    classify_role,
    comparable_hourly_pay,
    fallback_regular_spec,
    load_message_cache,
    rerank_records,
    write_markdown,
    write_message_cache,
)


REPORT_PATH = Path(
    "artifacts/albamon/2026-08-01-combined-reranked-no-endtime-preference.json"
)


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


if __name__ == "__main__":
    unittest.main()
