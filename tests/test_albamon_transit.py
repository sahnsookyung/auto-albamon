import unittest
from unittest.mock import patch

from tools.albamon_transit import (
    build_cache,
    fastest_route,
    job_coordinates,
    reclassify_cache,
    route_cache_key,
    summarize_route,
    valid_korean_coordinates,
)


class AlbamonTransitTests(unittest.TestCase):
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

    def test_fastest_public_transit_route_is_selected(self):
        payload = {
            "routes": [
                {"properties": {"totalTime": 3700}},
                {"properties": {"totalTime": 2400}},
            ]
        }

        self.assertEqual(2400, fastest_route(payload)["properties"]["totalTime"])

    def test_fifty_minutes_is_excluded(self):
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

        self.assertTrue(result["distance_excluded"])
        self.assertEqual(50, result["transit_minutes"])

    def test_forty_nine_minutes_is_allowed(self):
        result = summarize_route(
            {"routes": [{"properties": {"totalTime": 2940}}]}
        )

        self.assertFalse(result["distance_excluded"])
        self.assertEqual(49, result["transit_minutes"])

    def test_route_rounded_up_to_fifty_minutes_is_excluded(self):
        result = summarize_route(
            {"routes": [{"properties": {"totalTime": 2941}}]}
        )

        self.assertTrue(result["distance_excluded"])
        self.assertEqual(50, result["transit_minutes"])

    def test_existing_cache_can_be_reclassified_without_api_call(self):
        cache = {
            "jobs": {
                "1": {
                    "route_status": "verified",
                    "transit_seconds": 2940,
                    "distance_excluded": False,
                },
                "2": {
                    "route_status": "verified",
                    "transit_seconds": 3000,
                    "distance_excluded": False,
                },
            }
        }

        result = reclassify_cache(cache)

        self.assertFalse(result["jobs"]["1"]["distance_excluded"])
        self.assertTrue(result["jobs"]["2"]["distance_excluded"])
        self.assertEqual(1, result["excluded_count"])
        self.assertEqual("<", result["cutoff_operator"])

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
        geocode.assert_called_once_with(api_key="test-key", address="삼각지")


if __name__ == "__main__":
    unittest.main()
