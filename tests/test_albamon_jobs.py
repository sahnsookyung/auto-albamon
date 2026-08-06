import json
import unittest

from datetime import date

from tools.albamon_jobs import (
    assess_early_weekend_shift_options,
    assess_duration_requirement,
    assess_hard_requirements,
    assess_logistics_job,
    assess_saturday_and_sunday_only,
    assess_weekend_compatible,
    assess_driving_license_requirement,
    build_search_payload,
    end_time_info,
    demographic_compatible,
    education_compatible,
    is_saturday_and_sunday_only,
    merge_job_detail_api_parts,
    normalize_enums,
    parse_next_data,
    structured_month_or_longer,
    work_term_has_ended,
)


class AlbamonNormalizationTests(unittest.TestCase):
    def test_normalize_enums_preserves_false_and_empty_values(self):
        value = {
            "week": {
                "key": "SATURDAY_AND_SUNDAY",
                "value": "40",
                "description": "주말(토,일)",
            },
            "flag": False,
            "empty": "",
            "missing": None,
        }

        self.assertEqual(
            {
                "week": "SATURDAY_AND_SUNDAY",
                "flag": False,
                "empty": "",
            },
            normalize_enums(value),
        )

    def test_strict_payload_overrides_negotiable_weekdays(self):
        raw = {
            "condition": {"excludeNegoWorkWeek": False},
            "pagination": {"page": 1, "size": 20},
        }

        payload = build_search_payload(
            raw, page=3, page_size=100, strict_weekdays=True
        )

        self.assertTrue(payload["condition"]["excludeNegoWorkWeek"])
        self.assertEqual({"page": 3, "size": 100}, payload["pagination"])


class AlbamonScheduleTests(unittest.TestCase):
    def test_requires_exactly_saturday_and_sunday(self):
        self.assertTrue(
            is_saturday_and_sunday_only(
                {"value": "sat,sun", "description": "토, 일"}
            )
        )
        self.assertTrue(
            is_saturday_and_sunday_only(
                {"value": "sun,sat", "description": "일, 토"}
            )
        )
        self.assertFalse(
            is_saturday_and_sunday_only(
                {"value": "fri,sat,sun", "description": "금, 토, 일"}
            )
        )

    def test_accepts_generic_weekend_detail_field(self):
        matches, reason = assess_saturday_and_sunday_only(
            {
                "workDays": {"value": "40", "description": "주말(토, 일)"},
                "workWeek": None,
                "workTermDescription": "08.01(토) ~ 08.02(일)",
                "recruitTitle": "주말 스태프",
            }
        )

        self.assertTrue(matches)
        self.assertIn("weekend", reason)

    def test_rejects_contradictory_weekend_metadata(self):
        base = {
            "workDays": {"value": "40", "description": "주말(토, 일)"},
            "workWeek": None,
        }
        friday = {**base, "workWeekEtc": "금요일"}
        saturday_only = {**base, "workTermDescription": "08.01(토)"}
        mixed_title = {**base, "recruitTitle": "주말 및 평일 단기 스태프"}

        self.assertFalse(assess_saturday_and_sunday_only(friday)[0])
        self.assertFalse(assess_saturday_and_sunday_only(saturday_only)[0])
        self.assertFalse(assess_saturday_and_sunday_only(mixed_title)[0])
        self.assertFalse(
            is_saturday_and_sunday_only(
                {"value": "sat", "description": "토"}
            )
        )

    def test_rejects_friday_in_posting_body_despite_weekend_field(self):
        compatible, reason = assess_saturday_and_sunday_only(
            {
                "workDays": {"value": "40", "description": "주말(토, 일)"},
                "content": "근무요일: 금(8/8부터 근무시작) 근무시간: 15:00~22:00",
            }
        )

        self.assertFalse(compatible)
        self.assertIn("different weekdays", reason)

    def test_end_before_is_strict_and_marks_overnight(self):
        early = end_time_info("09:00", "16:30", threshold="17:00")
        at_threshold = end_time_info("09:00", "17:00", threshold="17:00")
        overnight = end_time_info("22:00", "08:00", threshold="17:00")
        negotiable = end_time_info("", "", threshold="17:00")

        self.assertTrue(early.ends_before)
        self.assertFalse(at_threshold.ends_before)
        self.assertTrue(overnight.ends_before)
        self.assertTrue(overnight.overnight)
        self.assertIsNone(negotiable.ends_before)
        self.assertIsNone(negotiable.overnight)

    def test_finds_early_option_in_weekend_multi_shift_text(self):
        view_data = {
            "recruitTitle": "주말 파트타이머",
            "workDays": {"value": "40", "description": "주말(토, 일)"},
            "content": "근무타임 A: 09:30 ~ 16:30 / C: 16:30 ~ 22:30",
        }

        evidence = assess_early_weekend_shift_options(
            view_data, threshold="17:00"
        )

        self.assertTrue(evidence)

    def test_early_weekday_option_is_not_attributed_to_weekend_role(self):
        view_data = {
            "recruitTitle": "평일 마감, 주말 마감, 목금 오픈 선택",
            "workDays": {"value": "50", "description": "요일협의"},
            "content": (
                "주말 마감 12:00~20:00 / 목, 금 오픈 08:00~16:00 선택"
            ),
        }

        evidence = assess_early_weekend_shift_options(
            view_data, threshold="17:00"
        )

        self.assertFalse(evidence)

    def test_lunch_break_is_not_an_early_shift(self):
        view_data = {
            "recruitTitle": "주말 단기 알바",
            "workDays": {"value": "40", "description": "주말(토, 일)"},
            "content": "근무 12시~19시 (식사시간 2시~3시)",
        }

        evidence = assess_early_weekend_shift_options(
            view_data, threshold="17:00"
        )

        self.assertFalse(evidence)

    def test_structured_month_bucket_is_only_a_triage_signal(self):
        self.assertFalse(
            structured_month_or_longer(
                {"value": "20", "description": "1주일~1개월"}
            )
        )
        self.assertTrue(
            structured_month_or_longer(
                {"value": "30", "description": "1개월~3개월"}
            )
        )

    def test_stale_term_must_end_before_run_date(self):
        run_date = date(2026, 8, 1)

        self.assertTrue(work_term_has_ended("2026-07-31", as_of=run_date))
        self.assertFalse(work_term_has_ended("2026-08-01", as_of=run_date))
        self.assertFalse(work_term_has_ended(None, as_of=run_date))

    def test_negotiable_schedule_needs_a_verified_weekend_option(self):
        negotiable = {
            "workDays": {"value": "50", "description": "요일협의"},
            "content": "근무요일: 주말(토,일)",
        }
        vague = {
            "workDays": {"value": "50", "description": "요일협의"},
            "content": "근무요일 협의",
        }

        self.assertTrue(assess_weekend_compatible(negotiable)[0])
        self.assertFalse(assess_weekend_compatible(vague)[0])

    def test_negotiable_friday_saturday_schedule_is_not_a_full_weekend(self):
        view_data = {
            "recruitTitle": "주말 홀서빙",
            "workDays": {"value": "50", "description": "요일협의"},
            "content": "근무요일: 금요일 토요일 시급 12000 근무시간: 21시~03시",
        }

        compatible, basis = assess_weekend_compatible(view_data)

        self.assertFalse(compatible)
        self.assertIn("does not offer both Sat and Sun", basis)

    def test_dated_saturday_only_event_is_not_a_full_weekend(self):
        compatible, basis = assess_weekend_compatible(
            {
                "recruitTitle": "주말 하객 알바",
                "workDays": {"value": "50", "description": "요일협의"},
                "content": "일정: 8/8 (토) 시간: 약 1시간",
            }
        )

        self.assertFalse(compatible)
        self.assertIn("dated schedule", basis)

    def test_weekday_training_and_friday_weekend_are_not_weekend_only(self):
        weekday_training = {
            "recruitTitle": "주말 판매 알바",
            "workDays": {"value": "40", "description": "주말(토, 일)"},
            "content": "토,일 근무. 평일 교육 가능자로 모집합니다.",
        }
        friday_weekend = {
            "recruitTitle": "주말 팝업 근무자",
            "workDays": {"value": "50", "description": "요일협의"},
            "content": "주말 오픈(금/토/일) 근무자 09:30~17:00",
        }

        self.assertFalse(assess_weekend_compatible(weekday_training)[0])
        compatible, basis = assess_weekend_compatible(friday_weekend)
        self.assertFalse(compatible)
        self.assertIn("Friday", basis)

    def test_selectable_full_week_and_ampersand_weekend_remain_compatible(self):
        selectable = {
            "recruitTitle": "행사 판매크루",
            "workDays": {"value": "50", "description": "요일협의"},
            "content": "월,화,수,목,금 토,일중 선택요일 지원가능",
        }
        ampersand = {
            "recruitTitle": "주말 판매",
            "workDays": {"value": "50", "description": "요일협의"},
            "content": "근무요일: 요일협의(토&일 주말 근무도 가능) 근무시간: 10:00~19:00",
        }

        self.assertTrue(assess_weekend_compatible(selectable)[0])
        self.assertTrue(assess_weekend_compatible(ampersand)[0])

    def test_three_or_more_days_is_not_weekend_only(self):
        compatible, basis = assess_weekend_compatible(
            {
                "recruitTitle": "주말 판매",
                "workDays": {"value": "50", "description": "요일협의"},
                "content": "근무일: 스케줄제(협의)/주3일이상 근무시간: 10:00~16:00",
            }
        )

        self.assertFalse(compatible)
        self.assertIn("multi-day", basis)

    def test_named_required_days_omitting_sunday_are_not_weekend_compatible(self):
        view_data = {
            "recruitTitle": "주말 홀서빙",
            "workDays": {"value": "50", "description": "요일협의"},
            "content": (
                "근무요일: 요일 협의 근무시간: 월,화,목,금,토요일 17:00~22:00 "
                "월요일, 화요일, 목요일, 금요일, 토요일 근무 가능한 분 "
                "최소 주 2회(토, 일)"
            ),
        }

        compatible, basis = assess_weekend_compatible(view_data)

        self.assertFalse(compatible)
        self.assertIn("omits Sat or Sun", basis)

    def test_fixed_adjacent_saturday_sunday_dates_are_weekend_compatible(self):
        view_data = {
            "workDays": {"value": "50", "description": "요일협의"},
            "workTermDescription": "08.08(토) ~ 08.09(일)",
            "workTermStartDate": "2026-08-08",
            "workTermEndDate": "2026-08-09",
            "content": "행사 일자: 8월 8일~9일",
        }

        compatible, basis = assess_weekend_compatible(view_data)

        self.assertTrue(compatible)
        self.assertIn("Saturday-Sunday", basis)

    def test_separate_weekend_role_in_mixed_recruitment_is_compatible(self):
        view_data = {
            "workDays": {"value": "50", "description": "요일협의"},
            "content": "근무요일: 요일협의(평일/주말 모두 구합니다)",
        }

        compatible, basis = assess_weekend_compatible(view_data)

        self.assertTrue(compatible)
        self.assertIn("separate weekend role", basis)

    def test_weekend_title_does_not_override_required_five_days(self):
        view_data = {
            "recruitTitle": "주말 가능 단기 알바",
            "workDays": {"value": "40", "description": "주말(토, 일)"},
            "content": "근무요일: 주5일 고정 스케줄 근무(주말 포함)",
        }

        compatible, basis = assess_weekend_compatible(view_data)

        self.assertFalse(compatible)
        self.assertIn("multi-day", basis)

    def test_separate_sat_sun_role_survives_other_weekday_roles(self):
        view_data = {
            "recruitTitle": "평일 및 주말 모집",
            "workDays": {"value": "50", "description": "요일협의"},
            "content": (
                "근무요일: 요일협의(월-금(2명) / 토-일(2명)) "
                "평일 주5일 근무"
            ),
        }

        compatible, _ = assess_weekend_compatible(view_data)

        self.assertTrue(compatible)

    def test_weekend_exclusion_in_title_overrides_negotiable_tag(self):
        view_data = {
            "recruitTitle": "면접 진행요원 모집(주말제외)",
            "workDays": {"value": "50", "description": "요일협의"},
            "content": "8.5.(수)~8.13.(목) 진행",
        }

        compatible, basis = assess_weekend_compatible(view_data)

        self.assertFalse(compatible)
        self.assertIn("excludes weekend", basis)


class AlbamonEligibilityTests(unittest.TestCase):
    def test_demographic_filter_allows_male_or_unrestricted_age_26(self):
        unrestricted = {
            "gender": {"value": "0", "description": "성별무관"},
            "startAge": 0,
            "endAge": 0,
        }
        female_only = {
            "gender": {"value": "F", "description": "여자"},
            "startAge": 20,
            "endAge": 30,
        }

        self.assertTrue(demographic_compatible(unrestricted, age=26)[0])
        self.assertFalse(demographic_compatible(female_only, age=26)[0])

    def test_education_filter_requires_unrestricted_education(self):
        unrestricted = {"educationLevels": ["학력무관"]}
        college = {"educationLevels": ["대학(2,3년제) 졸업 이상"]}

        self.assertTrue(education_compatible(unrestricted)[0])
        compatible, basis = education_compatible(college)
        self.assertFalse(compatible)
        self.assertIn("대학(2,3년제)", basis)

    def test_logistics_classifier_keeps_retail_packaging_out(self):
        retail = {
            "recruitTitle": "베이커리 판매",
            "content": "빵 포장 및 고객응대, 재고관리",
        }
        coupang = {
            "recruitTitle": "쿠팡 단순소분",
            "content": "Sub Hub 상품 분류",
        }

        self.assertFalse(assess_logistics_job({}, retail)[0])
        self.assertTrue(assess_logistics_job({"isCoupang": True}, coupang)[0])

    def test_logistics_classifier_keeps_casting_notice_out(self):
        casting = {
            "recruitTitle": "연예인과 함께 보조출연 단역 촬영스탭",
            "part": ["방송스텝·촬영보조", "입출고·창고관리"],
            "content": "드라마와 영화 보조출연자를 모집합니다.",
        }

        self.assertFalse(assess_logistics_job({}, casting)[0])

    def test_duration_uses_explicit_content_minimum(self):
        two_months = assess_duration_requirement(
            {
                "workPeriod": {"value": "30", "description": "1개월~3개월"},
                "content": "근무기간 : 최소 2개월+연장협의(장기근무 가능)",
            }
        )
        six_months = assess_duration_requirement(
            {"content": "6개월 이상 근무하실 수 있는 분을 구합니다."}
        )

        self.assertEqual(2, two_months.minimum_months)
        self.assertTrue(two_months.hard_minimum)
        self.assertEqual(6, six_months.minimum_months)

    def test_duration_does_not_trust_conflicting_structured_bucket(self):
        assessment = assess_duration_requirement(
            {
                "workPeriod": {"value": "30", "description": "1개월~3개월"},
                "content": "근무기간: 1주일이하(협의가능)",
            }
        )

        self.assertFalse(assessment.hard_minimum)
        self.assertIsNone(assessment.minimum_months)
        self.assertEqual("verified_shorter", assessment.status)

    def test_negotiable_month_range_is_not_a_hard_minimum(self):
        assessment = assess_duration_requirement(
            {"content": "근무기간: 1개월~3개월(협의가능)"}
        )

        self.assertFalse(assessment.hard_minimum)
        self.assertIsNone(assessment.minimum_months)
        self.assertEqual("negotiable", assessment.status)

    def test_longer_negotiable_range_without_short_option_is_a_minimum(self):
        assessment = assess_duration_requirement(
            {"content": "근무기간: 3개월~6개월(협의가능)"}
        )

        self.assertTrue(assessment.hard_minimum)
        self.assertEqual(3, assessment.minimum_months)

    def test_duration_detects_popup_range_and_probation_commitment(self):
        popup = assess_duration_requirement(
            {"content": "7/21 오픈하는 팝업행사입니다. (2-3개월)"}
        )
        probation = assess_duration_requirement(
            {
                "content": (
                    "근무기간: 1주일~1개월. 1개월 수습이후 연장여부 검토. "
                    "오래일할 직원 뽑습니다."
                )
            }
        )

        self.assertTrue(popup.hard_minimum)
        self.assertEqual(2, popup.minimum_months)
        self.assertTrue(probation.hard_minimum)
        self.assertEqual(1, probation.minimum_months)

    def test_duration_negotiation_must_be_adjacent_to_duration(self):
        assessment = assess_duration_requirement(
            {
                "content": (
                    "근무기간: 1~3개월 근무요일: 월-일(요일 협의)"
                )
            }
        )

        self.assertTrue(assessment.hard_minimum)
        self.assertEqual(1, assessment.minimum_months)

    def test_duration_recognizes_extra_month_character_typo(self):
        assessment = assess_duration_requirement(
            {"content": "근무기간:3월개월이상(협의가능)"}
        )

        self.assertTrue(assessment.hard_minimum)
        self.assertEqual(3, assessment.minimum_months)

    def test_fixed_calendar_period_is_measured_from_posting_text(self):
        assessment = assess_duration_requirement(
            {
                "content": (
                    "근무 기간: 8월 01일~8월 31일 "
                    "전일정 근무 가능하신 분만 지원"
                )
            }
        )

        self.assertEqual(1, assessment.minimum_months)
        self.assertTrue(assessment.hard_minimum)
        self.assertIn("31-day", assessment.summary)

        slash_dates = assess_duration_requirement(
            {"content": "근무기간: 8/1(토) ~ 8/30(일)"}
        )
        self.assertEqual(1, slash_dates.minimum_months)
        self.assertTrue(slash_dates.hard_minimum)

    def test_title_month_commitments_are_hard_minimums(self):
        exact = assess_duration_requirement(
            {"recruitTitle": "[토일PT/1개월] 체험 지도 선생님"}
        )
        calendar_range = assess_duration_requirement(
            {"recruitTitle": "웨딩박람회 안내 도우미-9~10월 주말"}
        )

        self.assertTrue(exact.hard_minimum)
        self.assertEqual(1, exact.minimum_months)
        self.assertTrue(calendar_range.hard_minimum)
        self.assertEqual(2, calendar_range.minimum_months)

    def test_regular_employee_title_is_held_out(self):
        assessment = assess_duration_requirement(
            {"recruitTitle": "[태극당 청량리역점] 정규직 모집"}
        )

        self.assertFalse(assessment.hard_minimum)
        self.assertEqual("non_numeric_requirement", assessment.status)

    def test_unreadable_month_bucket_remains_unverified(self):
        assessment = assess_duration_requirement(
            {
                "workPeriod": {"value": "30", "description": "1개월~3개월"},
                "content": '<img src="posting.png">',
            }
        )

        self.assertFalse(assessment.hard_minimum)
        self.assertEqual("unverified", assessment.status)

    def test_detects_explicit_licence_and_driving_duties(self):
        explicit = {"content": "자격요건: 운전면허증 소지"}
        valet = {"recruitTitle": "주말 행사 발렛 근무자"}

        self.assertTrue(assess_driving_license_requirement(explicit)[0])
        self.assertTrue(assess_driving_license_requirement(valet)[0])

    def test_does_not_treat_driving_preference_as_a_requirement(self):
        bakery = {
            "recruitTitle": "주말 베이커리 파트타이머",
            "preferences": "운전 가능, 유사업무 경험 우대",
            "content": "우대조건: 운전가능",
        }

        self.assertFalse(assess_driving_license_requirement(bakery)[0])

    def test_hard_requirements_keep_preferences_separate(self):
        view_data = {
            "content": (
                "자격요건: 카페 근무 경험자 "
                "보건증 발급 가능하신 분 (필수) "
                "카페 경력자 우대 업무 관련 자격증 소지 우대"
            )
        }
        duration = assess_duration_requirement(view_data)

        requirements, certificates, _, _ = assess_hard_requirements(
            view_data, duration
        )

        self.assertIn("Café work experience", requirements)
        self.assertIn("Health certificate (보건증)", certificates)
        self.assertNotIn("Barista qualification", requirements)

    def test_experience_only_title_is_a_hard_requirement(self):
        view_data = {
            "recruitTitle": "경력직만 지원요망 카페 파트너",
            "content": "매장 운영 및 고객 응대",
        }
        duration = assess_duration_requirement(view_data)

        requirements, _, _, _ = assess_hard_requirements(view_data, duration)

        self.assertIn(
            "Relevant experience (posting title specifies experience)",
            requirements,
        )

    def test_experience_preferred_in_title_is_not_a_hard_requirement(self):
        view_data = {
            "recruitTitle": "메가커피 주말 알바 구인(경력자 우대)",
            "content": "메가커피 경력자 우대",
        }
        duration = assess_duration_requirement(view_data)

        requirements, _, _, _ = assess_hard_requirements(view_data, duration)

        self.assertNotIn(
            "Relevant experience (posting title specifies experience)",
            requirements,
        )

    def test_flyer_application_requirements_are_reported(self):
        view_data = {
            "content": "연령무관(미성년자불가) 출석대조용 전체모습사진 보내주세요",
        }
        duration = assess_duration_requirement(view_data)

        requirements, _, _, _ = assess_hard_requirements(view_data, duration)

        self.assertIn("Minors cannot apply", requirements)
        self.assertIn("Full-body photo with application", requirements)

    def test_lifeguard_certificate_is_a_hard_requirement(self):
        view_data = {
            "content": "자격 : 라이프가드 자격증 등 관련 자격증",
        }
        duration = assess_duration_requirement(view_data)

        requirements, certificates, _, _ = assess_hard_requirements(
            view_data, duration
        )

        self.assertIn("Lifeguard or related safety qualification", requirements)
        self.assertIn("Lifeguard or related safety qualification", certificates)

    def test_marine_operator_licences_are_a_hard_requirement(self):
        view_data = {
            "content": (
                "동력수상레저1급/요트자격증/6급항해사 중 한개 이상 자격증만 "
                "있어도 지원 가능 자격요건: 동력수상레저1급 또는 요트자격증"
            ),
        }
        duration = assess_duration_requirement(view_data)

        requirements, certificates, _, _ = assess_hard_requirements(
            view_data, duration
        )

        self.assertIn(
            "One of: powerboat operator class 1, yacht licence, or deck officer class 6",
            requirements,
        )
        self.assertIn("Powerboat/yacht/deck-officer licence", certificates)


class AlbamonDetailParsingTests(unittest.TestCase):
    def test_compact_api_parts_merge_to_the_detail_shape(self):
        merged = merge_job_detail_api_parts(
            123,
            {"recruitNo": 123, "recruitTitle": "주말 알바"},
            {"content": "근무요일: 토요일, 일요일"},
        )

        self.assertEqual(123, merged["recruitNo"])
        self.assertIn("토요일", merged["content"])

    def test_extracts_view_data_from_next_payload(self):
        page_data = {
            "props": {
                "pageProps": {
                    "fetchError": None,
                    "data": {"viewData": {"recruitNo": 123, "workEndTime": "16:00"}},
                }
            }
        }
        document = (
            '<html><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(page_data)
            + "</script></html>"
        )

        self.assertEqual(
            {"recruitNo": 123, "workEndTime": "16:00"},
            parse_next_data(document, 123),
        )

    def test_rejects_missing_structured_data(self):
        with self.assertRaisesRegex(Exception, "structured page data"):
            parse_next_data("<html></html>", 123)


if __name__ == "__main__":
    unittest.main()
