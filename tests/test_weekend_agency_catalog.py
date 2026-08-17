import unittest

from tools.albamon_dashboard import build_weekend_agency_payload
from tools.weekend_agencies.agencies import (
    HOTEL_AGENCIES,
    hotel_agency_catalog_payload,
    hotel_agency_source_coverage_payload,
    resolve_hotel_agency,
)


class HotelAgencyCatalogTests(unittest.TestCase):
    def test_catalog_preserves_supplied_hotels_and_specialties_without_ratings(self):
        catalog = {agency.agency_id: agency for agency in HOTEL_AGENCIES}

        self.assertEqual(
            ("로카우스", "코엑스 인터컨티넨탈", "그랜드 인터컨티넨탈", "아모리스"),
            catalog["dayagency"].featured_hotels,
        )
        self.assertEqual(
            ("신라호텔", "조선호텔", "서울드래곤시티", "리버사이드"),
            catalog["huplux"].featured_hotels,
        )
        self.assertEqual(
            ("JW메리어트", "콘래드", "서울드래곤시티", "리버사이드"),
            catalog["eumplus"].featured_hotels,
        )
        self.assertEqual(
            ("그랜드하얏트", "롯데호텔서울", "웨스틴조선", "페어몬트", "힐튼/그래비티 판교"),
            catalog["withyou"].featured_hotels,
        )
        self.assertEqual(("워커힐",), catalog["htservice"].featured_hotels)
        self.assertEqual(("연회", "뷔페", "주방"), catalog["htservice"].specialties)
        self.assertEqual(
            ("호텔", "웨딩/컨벤션", "단기 알바"), catalog["eoullim"].specialties
        )
        self.assertIn("포시즌스 호텔", catalog["rutine"].featured_hotels)

    def test_existing_source_ids_and_display_aliases_resolve_to_canonical_entries(self):
        expected = {
            "huplux": "huplux",
            "HUPLUX": "huplux",
            "휴플럭스": "huplux",
            "eumplus": "eumplus",
            "이음 플러스": "eumplus",
            "withyou": "withyou",
            "with_you": "withyou",
            "호텔서비스": "htservice",
            "HT Service": "htservice",
            "oulim": "eoullim",
            "routine": "rutine",
            "루틴": "rutine",
        }

        self.assertEqual(
            expected,
            {
                alias: resolve_hotel_agency(alias).agency_id
                for alias in expected
            },
        )
        self.assertIsNone(resolve_hotel_agency("unknown-agency"))

    def test_payload_is_json_safe_and_returns_independent_copies(self):
        first = hotel_agency_catalog_payload()
        first[0]["featured_hotels"].append("테스트")
        second = hotel_agency_catalog_payload()

        self.assertEqual(7, len(second))
        self.assertNotIn("테스트", second[0]["featured_hotels"])
        self.assertTrue(all("routine_competition" not in item for item in second))

    def test_dashboard_adds_catalog_when_reading_an_older_report(self):
        payload = build_weekend_agency_payload({"jobs": []})

        self.assertEqual(7, len(payload["agency_catalog"]))
        self.assertEqual(
            ["dayagency", "huplux", "eumplus", "withyou", "htservice", "eoullim", "rutine"],
            [entry["agency_id"] for entry in payload["agency_catalog"]],
        )
        self.assertEqual(
            "catalog_only_auth_required",
            payload["agency_source_coverage"]["dayagency"]["status"],
        )

    def test_source_coverage_does_not_claim_login_gated_dayagency_is_live(self):
        coverage = hotel_agency_source_coverage_payload()

        self.assertEqual("catalog_only_auth_required", coverage["dayagency"]["status"])
        self.assertEqual("catalog_only_low_yield", coverage["htservice"]["status"])
        self.assertEqual("configured_public_source", coverage["eoullim"]["status"])
        self.assertEqual("configured_public_source", coverage["rutine"]["status"])


if __name__ == "__main__":
    unittest.main()
