import json
import unittest
import urllib.error
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from tools.albamon_transit import (
    KakaoQuotaBudget,
    KakaoQuotaExhausted,
    KakaoRequestPacer,
    KAKAO_RATE_LIMIT_COOLDOWN_SECONDS,
    ORIGIN,
    build_late_night_journeys,
    build_cache,
    cache_origin_matches,
    canonical_worksite_address,
    fastest_route,
    geocode_address,
    geocode_query_candidates,
    job_coordinates,
    late_night_route_for_job,
    late_night_shift_ranges,
    load_commute_origin,
    load_late_night_route_cache,
    merge_late_night_routes,
    reclassify_cache,
    read_kakao_route,
    route_cache_key,
    save_commute_origin,
    summarize_route,
    valid_korean_coordinates,
)


class AlbamonTransitTests(unittest.TestCase):
    def test_remote_job_skips_kakao_and_is_never_distance_excluded(self):
        report = {
            "jobs": [
                {
                    "recruit_no": 1,
                    "title": "재택 문서 정리",
                    "company": "테스트",
                    "area": "재택",
                    "working_address": "",
                    "url": "https://example.test/1",
                    "remote_job": True,
                    "eligibility_lanes": ["remote_short_term"],
                }
            ],
            "logistics_jobs": [],
        }

        cache = build_cache(report, api_key="", workers=1)

        self.assertEqual(0, cache["route_request_count"])
        self.assertEqual(1, cache["unique_route_count"])
        self.assertEqual("not_required", cache["jobs"]["1"]["route_status"])
        self.assertEqual(0, cache["jobs"]["1"]["transit_minutes"])
        self.assertFalse(cache["jobs"]["1"]["distance_excluded"])

    def test_geocode_candidates_remove_parenthetical_floor_annotations(self):
        self.assertEqual(
            [
                "서울 용산구 한강대로38길 23 (용산동5가) 1,2,3층",
                "서울 용산구 한강대로38길 23",
            ],
            geocode_query_candidates(
                "서울 용산구 한강대로38길 23 (용산동5가) 1,2,3층"
            ),
        )

    def test_verified_venue_alias_reuses_canonical_road_address(self):
        self.assertEqual(
            "서울 서초구 신반포로 176",
            canonical_worksite_address(
                "서울 서초구 반포동 신세계 백화점 강남점"
            ),
        )
        named_venue = {
            "recruit_no": 1,
            "working_address": "서울 서초구 반포동 신세계 백화점 강남점",
        }
        road_address = {
            "recruit_no": 2,
            "working_address": "서울 서초구 신반포로 176, 신세계백화점 플리츠무드",
        }

        self.assertEqual(route_cache_key(named_venue), route_cache_key(road_address))

    @patch("tools.albamon_transit.urllib.request.urlopen")
    def test_geocode_retries_a_clean_road_address(self, urlopen):
        missing = MagicMock()
        missing.__enter__.return_value.read.return_value = b'{"documents":[]}'
        found = MagicMock()
        found.__enter__.return_value.read.return_value = (
            b'{"documents":[{"x":"126.968","y":"37.529"}]}'
        )
        urlopen.side_effect = [missing, found]
        pacer = MagicMock()

        coordinates = geocode_address(
            api_key="secret-not-persisted",
            address="서울 용산구 한강대로38길 23 (용산동5가) 1,2,3층",
            request_pacer=pacer,
        )

        self.assertEqual((37.529, 126.968), coordinates)
        self.assertEqual(2, urlopen.call_count)
        self.assertEqual(2, pacer.wait.call_count)
        second_url = urlopen.call_args_list[1].args[0].full_url
        self.assertNotIn("%28", second_url)

    def test_commute_origin_is_validated_persisted_and_loaded(self):
        original = dict(ORIGIN)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "origin.json"
            candidate = {
                "name": "강남역",
                "address": "서울특별시 강남구 강남대로 396",
                "latitude": 37.4979,
                "longitude": 127.0276,
            }
            try:
                saved = save_commute_origin(candidate, path=path)
                self.assertEqual(candidate, saved)
                self.assertEqual(candidate, load_commute_origin(path))
            finally:
                save_commute_origin(original, path=path)

    def test_kakao_request_pacer_spaces_calls_and_applies_shared_cooldown(self):
        clock = [10.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        pacer = KakaoRequestPacer(
            requests_per_second=4,
            monotonic=lambda: clock[0],
            sleeper=sleep,
        )

        self.assertEqual(0, pacer.wait())
        self.assertAlmostEqual(0.25, pacer.wait())
        pacer.defer(5)
        self.assertAlmostEqual(5, pacer.wait())

        self.assertEqual([0.25, 5], sleeps)

    def test_kakao_daily_budget_fails_closed_below_published_quota(self):
        now = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            budget = KakaoQuotaBudget(
                path,
                daily_limit=2,
                now_provider=lambda: now,
            )

            budget.reserve()
            budget.reserve()
            with self.assertRaisesRegex(KakaoQuotaExhausted, "exhausted"):
                budget.reserve()
            state = json.loads(path.read_text(encoding="utf-8"))
            snapshot = budget.snapshot()

        self.assertEqual(2, state["used"])
        self.assertEqual(0, snapshot["remaining"])
        self.assertNotIn("key", json.dumps(state).lower())

    def test_kakao_daily_budget_resets_on_the_next_korea_date(self):
        clock = [datetime(2026, 8, 12, 12, tzinfo=timezone.utc)]
        with TemporaryDirectory() as directory:
            budget = KakaoQuotaBudget(
                Path(directory) / "quota.json",
                daily_limit=1,
                now_provider=lambda: clock[0],
            )
            budget.reserve()
            clock[0] = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)

            budget.reserve()
            snapshot = budget.snapshot()

        self.assertEqual("2026-08-13", snapshot["date_kst"])
        self.assertEqual(1, snapshot["used"])

    @patch("tools.albamon_transit.urllib.request.urlopen")
    def test_kakao_429_stops_later_calls_without_retrying(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError(
            "https://dapi.kakao.com/v2/routing/publictraffic",
            429,
            "quota",
            {},
            None,
        )
        with TemporaryDirectory() as directory:
            budget = KakaoQuotaBudget(
                Path(directory) / "quota.json",
                daily_limit=10,
            )

            with self.assertRaises(KakaoQuotaExhausted):
                read_kakao_route(
                    api_key="secret-not-persisted",
                    latitude=37.5,
                    longitude=127.1,
                    quota_budget=budget,
                )
            with self.assertRaises(KakaoQuotaExhausted):
                read_kakao_route(
                    api_key="secret-not-persisted",
                    latitude=37.5,
                    longitude=127.1,
                    quota_budget=budget,
                )
            snapshot = budget.snapshot()

        self.assertEqual(1, urlopen.call_count)
        self.assertTrue(snapshot["remote_exhausted"])
        self.assertEqual(0, snapshot["remaining"])

    @patch("tools.albamon_transit.urllib.request.urlopen")
    def test_kakao_minus_ten_retries_once_then_stops_as_quota_exhaustion(
        self, urlopen
    ):
        urlopen.side_effect = [
            urllib.error.HTTPError(
                "https://dapi.kakao.com/v2/routing/publictraffic",
                400,
                "limit",
                {},
                BytesIO(b'{"code":-10,"msg":"API limit has been exceeded."}'),
            )
            for _ in range(2)
        ]
        pacer = MagicMock()
        with TemporaryDirectory() as directory:
            budget = KakaoQuotaBudget(
                Path(directory) / "quota.json",
                daily_limit=10,
            )

            with self.assertRaises(KakaoQuotaExhausted):
                read_kakao_route(
                    api_key="secret-not-persisted",
                    latitude=37.5,
                    longitude=127.1,
                    quota_budget=budget,
                    request_pacer=pacer,
                )
            snapshot = budget.snapshot()

        self.assertEqual(2, urlopen.call_count)
        self.assertEqual(2, pacer.wait.call_count)
        pacer.defer.assert_called_once_with(KAKAO_RATE_LIMIT_COOLDOWN_SECONDS)
        self.assertEqual("quota_-10", snapshot["blocked_reason"])
        self.assertEqual(0, snapshot["remaining"])

    @patch("tools.albamon_transit.urllib.request.urlopen")
    def test_kakao_minus_ten_succeeds_after_one_paced_cooldown(self, urlopen):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"status":"OK"}'
        urlopen.side_effect = [
            urllib.error.HTTPError(
                "https://dapi.kakao.com/v2/routing/publictraffic",
                400,
                "limit",
                {},
                BytesIO(b'{"code":-10,"msg":"API limit has been exceeded."}'),
            ),
            response,
        ]
        pacer = MagicMock()
        with TemporaryDirectory() as directory:
            budget = KakaoQuotaBudget(
                Path(directory) / "quota.json",
                daily_limit=10,
            )

            payload = read_kakao_route(
                api_key="secret-not-persisted",
                latitude=37.5,
                longitude=127.1,
                quota_budget=budget,
                request_pacer=pacer,
            )
            snapshot = budget.snapshot()

        self.assertEqual("OK", payload["status"])
        self.assertEqual(2, snapshot["used"])
        self.assertFalse(snapshot["remote_exhausted"])
        pacer.defer.assert_called_once_with(KAKAO_RATE_LIMIT_COOLDOWN_SECONDS)

    def test_http_access_block_can_be_retried_without_clearing_usage(self):
        with TemporaryDirectory() as directory:
            budget = KakaoQuotaBudget(
                Path(directory) / "quota.json",
                daily_limit=10,
            )
            budget.reserve()
            budget.mark_remote_exhausted("http_403")

            budget.clear_access_block()
            snapshot = budget.snapshot()

        self.assertEqual(1, snapshot["used"])
        self.assertEqual(9, snapshot["remaining"])
        self.assertFalse(snapshot["remote_exhausted"])

    def test_quota_exhaustion_marks_webui_fallback(self):
        job = {
            "recruit_no": 1,
            "title": "새 공고",
            "company": "회사",
            "area": "서울 송파구",
            "working_address": "서울 송파구 새말로 1",
            "latitude": 37.5,
            "longitude": 127.1,
            "url": "https://example/1",
        }
        with TemporaryDirectory() as directory:
            budget = KakaoQuotaBudget(
                Path(directory) / "quota.json",
                daily_limit=1,
            )
            budget.reserve()
            cache = build_cache(
                {"jobs": [job], "logistics_jobs": []},
                api_key="test-key",
                workers=1,
                quota_budget=budget,
                defer_unverified_routes=True,
            )

        self.assertEqual(0, cache["route_request_count"])
        self.assertEqual(1, cache["webui_fallback_required_count"])
        self.assertEqual("kakao_quota_exhausted", cache["webui_fallback_jobs"][0]["reason"])
        self.assertTrue(cache["jobs"]["1"]["route_webui_fallback_required"])

    def test_late_night_cache_rejects_future_leg_without_elapsed_wait(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "routes.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "origin": dict(ORIGIN),
                        "routes": [
                            {
                                "address_match": "오금로11길29",
                                "exact_shift_routes": {
                                    "20:00~02:00": {
                                        "outbound": {
                                            "displayed_route_minutes": 21
                                        }
                                    }
                                },
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "elapsed_minutes"):
                load_late_night_route_cache(path)

    def test_late_night_route_matches_exact_address_fragment(self):
        cache = {
            "routes": [
                {"address_match": "올림픽로32길19", "marker": "expected"},
                {"address_match": "마천로264", "marker": "other"},
            ]
        }

        route = late_night_route_for_job(
            {"working_address": "서울 송파구 올림픽로32길 19 (방이동) 2층"},
            cache,
        )

        self.assertEqual("expected", route["marker"])

    def test_exact_shift_times_choose_night_bus_and_keep_bike_fallback(self):
        job = {
            "late_night_shift_options": [
                "20:00~23:00 — early close",
                "20:00~02:00 — overnight",
            ]
        }
        route = {
            "outbound_transit_pattern_minutes": 22,
            "return_transit_pattern_minutes": 25,
            "night_transit_pattern": {"minutes": 24, "vehicles": ["N31(심야)"]},
            "bicycle_road_priority_minutes": 14,
            "bicycle_road_priority_distance_km": 3.1,
            "bicycle_shortest_minutes": 13,
            "bicycle_shortest_distance_km": 3.0,
        }

        journeys = build_late_night_journeys(job, route)

        self.assertEqual(
            [("20:00", "23:00"), ("20:00", "02:00")],
            late_night_shift_ranges(job),
        )
        self.assertEqual("public_transit_pattern", journeys[0]["return"]["mode"])
        self.assertEqual("night_bus_pattern", journeys[1]["return"]["mode"])
        self.assertEqual(["N31(심야)"], journeys[1]["return"]["vehicles"])
        self.assertEqual(14, journeys[1]["bicycle_fallback"]["minutes"])

    def test_late_night_shift_ranges_ignores_other_times_in_evidence(self):
        job = {
            "late_night_shift_options": [
                "20:00~23:00 — 미들(09:00-16:00)/마감(20:00-23:00)"
            ]
        }

        self.assertEqual([("20:00", "23:00")], late_night_shift_ranges(job))

    def test_browser_late_night_route_seeds_normal_cache(self):
        job = {
            "recruit_no": 1,
            "title": "마감",
            "company": "회사",
            "area": "서울 송파구",
            "working_address": "서울 송파구 오금로11길 29 1층",
            "url": "https://example/1",
            "eligibility_lanes": ["songpa_late_night"],
            "late_night_shift_options": ["20:00~02:00 — structured work time"],
        }
        late_cache = {
            "provider": "Kakao Maps web UI",
            "checked_at": "2026-08-11T22:00:00+09:00",
            "route_time_policy": "exact advertised times with bike fallback",
            "routes": [
                {
                    "address_match": "오금로11길29",
                    "checked_at": "2026-08-11T22:00:00+09:00",
                    "outbound_transit_pattern_minutes": 22,
                    "return_transit_pattern_minutes": 25,
                    "night_transit_pattern": {
                        "minutes": 24,
                        "vehicles": ["N31(심야)"],
                    },
                    "exact_shift_routes": {
                        "20:00~02:00": {
                            "service_date": "2026-08-15",
                            "outbound": {
                                "elapsed_minutes": 28,
                                "displayed_route_minutes": 21,
                                "departure_at": "2026-08-15T19:30:00+09:00",
                                "arrival_at": "2026-08-15T19:58:00+09:00",
                                "vehicles": ["341"],
                            },
                            "return": {
                                "elapsed_minutes": 38,
                                "displayed_route_minutes": 21,
                                "departure_at": "2026-08-16T02:00:00+09:00",
                                "arrival_at": "2026-08-16T02:38:00+09:00",
                                "vehicles": ["N31"],
                            },
                        }
                    },
                    "bicycle_road_priority_minutes": 14,
                    "bicycle_road_priority_distance_km": 3.1,
                    "bicycle_shortest_minutes": 13,
                    "bicycle_shortest_distance_km": 3.0,
                }
            ],
        }

        merged = merge_late_night_routes(
            {"jobs": [job], "logistics_jobs": []},
            {"origin": dict(ORIGIN), "routes": {}, "jobs": {}},
            late_cache,
        )
        route_result = merged["routes"][route_cache_key(job)]["result"]
        cache = build_cache(
            {"jobs": [job], "logistics_jobs": []},
            api_key="",
            workers=1,
            existing_cache=merged,
            now=datetime(2026, 8, 11, 14, tzinfo=timezone.utc),
        )
        result = cache["jobs"]["1"]

        self.assertEqual("verified", route_result["route_status"])
        self.assertNotIn("late_night_shift_journeys", route_result)
        self.assertEqual(22, route_result["transit_minutes"])
        self.assertEqual(28, result["transit_minutes"])
        self.assertEqual("NAVER_FUTURE_TIME_BUS_OR_SUBWAY", result["transit_type"])
        self.assertEqual("20:00~02:00", result["late_night_shift_journeys"][0]["shift"])
        self.assertEqual(
            "public_transit_future_time",
            result["late_night_shift_journeys"][0]["return"]["mode"],
        )
        self.assertEqual(38, result["late_night_shift_journeys"][0]["return"]["minutes"])
        self.assertEqual(14, result["return_bicycle_minutes"])

    def test_verified_future_return_unavailable_uses_bicycle(self):
        journeys = build_late_night_journeys(
            {"late_night_shift_options": ["20:00~02:00 — exact"]},
            {
                "outbound_transit_pattern_minutes": 22,
                "night_transit_pattern": {"minutes": 24, "vehicles": ["N31"]},
                "bicycle_road_priority_minutes": 14,
                "bicycle_road_priority_distance_km": 3.1,
                "exact_shift_routes": {
                    "20:00~02:00": {
                        "outbound": {
                            "elapsed_minutes": 28,
                            "departure_at": "2026-08-15T19:30:00+09:00",
                            "arrival_at": "2026-08-15T19:58:00+09:00",
                        },
                        "return": {
                            "available": False,
                            "departure_at": "2026-08-16T02:00:00+09:00",
                        },
                    }
                },
            },
        )

        self.assertEqual("bicycle_road_priority", journeys[0]["return"]["mode"])
        self.assertTrue(journeys[0]["return"]["future_transit_unavailable"])

    def test_shared_late_night_route_keeps_each_jobs_exact_shift(self):
        jobs = [
            {
                "recruit_no": 1,
                "title": "마감 A",
                "company": "회사",
                "area": "서울 송파구",
                "working_address": "서울 송파구 오금로11길 29 1층",
                "url": "https://example/1",
                "eligibility_lanes": ["songpa_late_night"],
                "late_night_shift_options": ["20:00~23:00 — A"],
            },
            {
                "recruit_no": 2,
                "title": "마감 B",
                "company": "회사",
                "area": "서울 송파구",
                "working_address": "서울 송파구 오금로11길 29 2층",
                "url": "https://example/2",
                "eligibility_lanes": ["songpa_late_night"],
                "late_night_shift_options": ["20:30~23:30 — B"],
            },
        ]
        late_cache = {
            "provider": "Kakao Maps web UI",
            "checked_at": "2026-08-11T22:00:00+09:00",
            "routes": [
                {
                    "address_match": "오금로11길29",
                    "checked_at": "2026-08-11T22:00:00+09:00",
                    "outbound_transit_pattern_minutes": 22,
                    "return_transit_pattern_minutes": 25,
                    "bicycle_road_priority_minutes": 14,
                    "bicycle_road_priority_distance_km": 3.1,
                }
            ],
        }

        merged = merge_late_night_routes(
            {"jobs": jobs, "logistics_jobs": []},
            {"origin": dict(ORIGIN), "routes": {}, "jobs": {}},
            late_cache,
        )
        cache = build_cache(
            {"jobs": jobs, "logistics_jobs": []},
            api_key="",
            workers=1,
            existing_cache=merged,
            now=datetime(2026, 8, 11, 14, tzinfo=timezone.utc),
        )

        self.assertEqual(
            "20:00~23:00",
            cache["jobs"]["1"]["late_night_shift_journeys"][0]["shift"],
        )
        self.assertEqual(
            "20:30~23:30",
            cache["jobs"]["2"]["late_night_shift_journeys"][0]["shift"],
        )

    def test_route_cache_groups_reposts_by_exact_address(self):
        jobs = [
            {
                "recruit_no": 1,
                "title": "주말 스태프",
                "company": "회사",
                "area": "서울 송파구",
                "working_address": "서울 송파구 올림픽로 1",
                "url": "https://example/1",
            },
            {
                "recruit_no": 2,
                "title": "주말 스태프 재공고",
                "company": "회사",
                "area": "서울 송파구",
                "working_address": "서울 송파구 올림픽로 1",
                "url": "https://example/2",
            },
        ]
        route_result = {
            **jobs[0],
            "route_status": "verified",
            "transit_seconds": 1200,
            "transit_minutes": 20,
            "distance_excluded": False,
        }
        with patch(
            "tools.albamon_transit.assess_job", return_value=route_result
        ) as assess:
            cache = build_cache(
                {"jobs": jobs, "logistics_jobs": []},
                api_key="test-key",
                workers=4,
            )

        assess.assert_called_once()
        self.assertEqual(1, cache["unique_route_count"])
        self.assertEqual(1, cache["route_request_count"])
        self.assertEqual(2, cache["job_count"])
        self.assertEqual(["bus", "subway/train"], cache["allowed_modes"])

        with patch("tools.albamon_transit.assess_job") as assess_again:
            warm_cache = build_cache(
                {"jobs": jobs, "logistics_jobs": []},
                api_key="test-key",
                workers=4,
                existing_cache=cache,
            )

        assess_again.assert_not_called()
        self.assertEqual(0, warm_cache["route_request_count"])
        self.assertEqual(2, warm_cache["route_cache_hit_count"])

    def test_broad_area_without_address_does_not_share_route(self):
        first = {"recruit_no": 1, "area": "서울 강남구"}
        second = {"recruit_no": 2, "area": "서울 강남구"}

        self.assertNotEqual(route_cache_key(first), route_cache_key(second))

    def test_browser_utc_z_timestamp_is_reused_on_python_39(self):
        job = {
            "recruit_no": 1,
            "title": "주말 스태프",
            "company": "회사",
            "area": "서울 송파구",
            "working_address": "서울 송파구 올림픽로 1",
            "url": "https://example/1",
        }
        key = route_cache_key(job)
        existing_cache = {
            "origin": dict(ORIGIN),
            "routes": {
                key: {
                    "checked_at": "2026-08-09T13:16:02.135Z",
                    "result": {
                        "route_status": "verified",
                        "transit_seconds": 1200,
                        "transit_minutes": 20,
                        "distance_excluded": False,
                        "route_cache_stale": True,
                        "route_reuse_basis": (
                            "reused stale browser-verified Kakao route; "
                            "refresh recommended"
                        ),
                        "route_source_checked_at": "2026-08-11T13:16:02.135Z",
                    },
                }
            },
        }

        with patch("tools.albamon_transit.assess_job") as assess:
            result = build_cache(
                {"jobs": [job], "logistics_jobs": []},
                api_key="test-key",
                workers=1,
                existing_cache=existing_cache,
                refresh_hour=0,
                now=datetime(2026, 8, 11, 14, tzinfo=timezone.utc),
            )

        assess.assert_not_called()
        self.assertEqual(1, result["route_cache_hit_count"])
        self.assertEqual(0, result["route_stale_reuse_count"])
        self.assertNotIn("route_cache_stale", result["jobs"]["1"])

    def test_recent_wrapper_timestamp_does_not_launder_stale_route(self):
        job = {
            "recruit_no": 1,
            "working_address": "서울 송파구 올림픽로 1",
        }
        key = route_cache_key(job)
        existing_cache = {
            "origin": dict(ORIGIN),
            "routes": {
                key: {
                    "checked_at": "2026-08-11T13:16:02.135Z",
                    "result": {
                        "route_status": "verified",
                        "transit_minutes": 20,
                        "route_source_checked_at": "2026-08-09T13:16:02.135Z",
                    },
                }
            },
        }

        result = build_cache(
            {"jobs": [job], "logistics_jobs": []},
            api_key="",
            workers=1,
            existing_cache=existing_cache,
            refresh_hour=0,
            reuse_stale_verified_routes=True,
            now=datetime(2026, 8, 11, 14, tzinfo=timezone.utc),
        )

        self.assertEqual(1, result["route_stale_reuse_count"])
        self.assertTrue(result["jobs"]["1"]["route_cache_stale"])

    def test_existing_routes_refresh_once_after_ten_am_anchor(self):
        job = {
            "recruit_no": 1,
            "title": "주말 스태프",
            "company": "회사",
            "area": "서울 송파구",
            "working_address": "서울 송파구 올림픽로 1",
            "url": "https://example/1",
        }
        route_result = {
            **job,
            "route_status": "verified",
            "transit_seconds": 1200,
            "transit_minutes": 20,
            "distance_excluded": False,
        }
        first_time = datetime(2026, 8, 9, 11, tzinfo=timezone.utc)
        with patch(
            "tools.albamon_transit.assess_job", return_value=route_result
        ):
            first = build_cache(
                {"jobs": [job], "logistics_jobs": []},
                api_key="test-key",
                workers=1,
                now=first_time,
            )

        with patch("tools.albamon_transit.assess_job") as before_anchor:
            warm = build_cache(
                {"jobs": [job], "logistics_jobs": []},
                api_key="test-key",
                workers=1,
                existing_cache=first,
                now=datetime(2026, 8, 10, 9, tzinfo=timezone.utc),
            )
        before_anchor.assert_not_called()
        self.assertEqual(1, warm["route_cache_hit_count"])

        with patch(
            "tools.albamon_transit.assess_job", return_value=route_result
        ) as after_anchor:
            refreshed = build_cache(
                {"jobs": [job], "logistics_jobs": []},
                api_key="test-key",
                workers=1,
                existing_cache=warm,
                now=datetime(2026, 8, 10, 10, tzinfo=timezone.utc),
            )
        after_anchor.assert_called_once()
        self.assertEqual(1, refreshed["route_request_count"])
        self.assertEqual(10, refreshed["route_refresh_hour_local"])

    def test_missing_api_key_fails_before_route_requests(self):
        job = {
            "recruit_no": 1,
            "title": "주말 스태프",
            "company": "회사",
            "area": "서울 송파구",
            "working_address": "서울 송파구 올림픽로 1",
            "url": "https://example/1",
        }

        with patch("tools.albamon_transit.assess_job") as assess:
            with self.assertRaisesRegex(ValueError, "KAKAO_REST_API_KEY"):
                build_cache(
                    {"jobs": [job], "logistics_jobs": []},
                    api_key="",
                    workers=1,
                )

        assess.assert_not_called()

    def test_scheduled_refresh_reuses_stale_browser_verified_route(self):
        job = {
            "recruit_no": 1,
            "title": "주말 스태프",
            "company": "회사",
            "area": "서울 송파구",
            "working_address": "서울 송파구 올림픽로 1",
            "url": "https://example/1",
        }
        key = route_cache_key(job)
        cache = {
            "origin": dict(ORIGIN),
            "routes": {
                key: {
                    "checked_at": "2026-08-01T10:00:00+09:00",
                    "result": {
                        "route_status": "verified",
                        "transit_seconds": 1200,
                        "transit_minutes": 20,
                        "distance_excluded": False,
                    },
                }
            },
        }

        result = build_cache(
            {"jobs": [job], "logistics_jobs": []},
            api_key="",
            workers=1,
            existing_cache=cache,
            reuse_stale_verified_routes=True,
            now=datetime(2026, 8, 11, 12, tzinfo=timezone.utc),
        )

        self.assertEqual(1, result["route_stale_reuse_count"])
        self.assertTrue(result["jobs"]["1"]["route_cache_stale"])
        self.assertEqual(0, result["route_request_count"])

    def test_scheduled_refresh_defers_only_unseen_worksite(self):
        job = {
            "recruit_no": 1,
            "title": "새 공고",
            "company": "회사",
            "area": "서울 송파구",
            "working_address": "서울 송파구 새말로 1",
            "url": "https://example/1",
        }

        result = build_cache(
            {"jobs": [job], "logistics_jobs": []},
            api_key="",
            workers=1,
            existing_cache={"origin": dict(ORIGIN), "routes": {}},
            defer_unverified_routes=True,
        )

        self.assertEqual(1, result["route_deferred_count"])
        self.assertEqual("unverified", result["jobs"]["1"]["route_status"])
        self.assertTrue(result["jobs"]["1"]["distance_excluded"])

    def test_fresh_api_fallback_is_retried_instead_of_cached_for_a_day(self):
        job = {
            "recruit_no": 1,
            "title": "새 공고",
            "company": "회사",
            "area": "서울 송파구",
            "working_address": "서울 송파구 새말로 1",
            "url": "https://example/1",
        }
        key = route_cache_key(job)
        existing = {
            "origin": dict(ORIGIN),
            "routes": {
                key: {
                    "checked_at": "2026-08-12T01:00:00+09:00",
                    "result": {
                        "route_status": "unverified",
                        "route_webui_fallback_required": True,
                        "route_fallback_reason": "kakao_api_unavailable",
                        "distance_excluded": True,
                    },
                }
            },
        }
        verified = {
            **job,
            "route_status": "verified",
            "transit_minutes": 20,
            "distance_excluded": False,
        }

        with patch(
            "tools.albamon_transit.assess_job", return_value=verified
        ) as assess:
            result = build_cache(
                {"jobs": [job], "logistics_jobs": []},
                api_key="test-key",
                workers=1,
                existing_cache=existing,
                now=datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
            )

        assess.assert_called_once()
        self.assertEqual(1, result["route_request_count"])
        self.assertEqual("verified", result["jobs"]["1"]["route_status"])

    def test_changed_origin_invalidates_every_cached_route(self):
        job = {
            "recruit_no": 1,
            "working_address": "서울 용산구 소월로 322",
        }
        old_cache = {
            "origin": {
                "name": "잠실새내역",
                "latitude": 37.5115,
                "longitude": 127.0867,
            },
            "routes": {
                route_cache_key(job): {
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "result": {
                        "route_status": "verified",
                        "transit_seconds": 1800,
                    },
                }
            },
        }

        self.assertFalse(cache_origin_matches(old_cache))
        with self.assertRaisesRegex(ValueError, "KAKAO_REST_API_KEY"):
            build_cache(
                {"jobs": [job], "logistics_jobs": []},
                api_key="",
                workers=1,
                existing_cache=old_cache,
            )

    def test_fastest_public_transit_route_is_selected(self):
        payload = {
            "routes": [
                {"properties": {"totalTime": 3700}},
                {"properties": {"totalTime": 2400}},
            ]
        }

        self.assertEqual(2400, fastest_route(payload)["properties"]["totalTime"])

    def test_fifty_minutes_is_allowed(self):
        payload = {
            "properties": {"landingURL": "https://map.example/route"},
            "routes": [
                {
                    "properties": {
                        "totalTime": 3000,
                        "type": "BUS_AND_SUBWAY",
                        "transfers": 1,
                        "fare": {"value": 1550},
                    },
                    "steps": [
                        {"properties": {"vehicles": [{"name": "2호선"}]}}
                    ],
                }
            ],
        }

        result = summarize_route(payload)

        self.assertFalse(result["distance_excluded"])
        self.assertEqual(50, result["transit_minutes"])

    def test_forty_nine_minutes_is_allowed(self):
        result = summarize_route(
            {"routes": [{"properties": {"totalTime": 2940}}]}
        )

        self.assertFalse(result["distance_excluded"])
        self.assertEqual(49, result["transit_minutes"])

    def test_route_rounded_up_to_fifty_minutes_is_allowed(self):
        result = summarize_route(
            {"routes": [{"properties": {"totalTime": 2941}}]}
        )

        self.assertFalse(result["distance_excluded"])
        self.assertEqual(50, result["transit_minutes"])

    def test_existing_cache_can_be_reclassified_without_api_call(self):
        cache = {
            "origin": dict(ORIGIN),
            "jobs": {
                "1": {
                    "route_status": "verified",
                    "transit_seconds": 2940,
                    "distance_excluded": False,
                },
                "2": {
                    "route_status": "verified",
                    "transit_seconds": 3001,
                    "distance_excluded": False,
                },
            }
        }

        result = reclassify_cache(cache)

        self.assertFalse(result["jobs"]["1"]["distance_excluded"])
        self.assertTrue(result["jobs"]["2"]["distance_excluded"])
        self.assertEqual(1, result["excluded_count"])
        self.assertEqual("<=", result["cutoff_operator"])

    def test_zero_coordinates_are_not_treated_as_a_real_worksite(self):
        self.assertFalse(valid_korean_coordinates(0, 0))
        self.assertTrue(valid_korean_coordinates(37.53, 126.97))

    @patch("tools.albamon_transit.geocode_address", return_value=(37.53, 126.97))
    @patch(
        "tools.albamon_transit.fetch_job_detail",
        return_value={"latitude": 0, "longitude": 0, "workingAddress": "삼각지"},
    )
    def test_invalid_detail_coordinates_fall_back_to_address_geocoding(
        self, _detail, geocode
    ):
        result = job_coordinates(
            {"recruit_no": 1, "latitude": 0, "longitude": 0},
            api_key="test-key",
        )

        self.assertEqual((37.53, 126.97), result)
        geocode.assert_called_once_with(
            api_key="test-key",
            address="삼각지",
            request_pacer=None,
            quota_budget=None,
        )


if __name__ == "__main__":
    unittest.main()
