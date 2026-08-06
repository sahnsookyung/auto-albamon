import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.albamon_cache import DetailCache, sanitize_detail_for_cache


class AlbamonDetailCacheTests(unittest.TestCase):
    def setUp(self):
        self.job = {
            "recruitNo": 123,
            "recruitTitle": "주말 홀서빙",
            "companyName": "테스트 식당",
            "pay": "15,000원",
            "workingWeek": "주말(토,일)",
        }
        self.detail = {
            "recruitNo": 123,
            "content": "근무요일: 토요일, 일요일",
            "phoneNumber": "not-retained",
            "managerEmail": "not-retained@example.com",
        }

    def test_unchanged_excluded_detail_is_reused_but_changed_or_active_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = DetailCache(Path(directory) / "details.json")
            now = datetime(2026, 8, 5, tzinfo=timezone.utc)
            cache.store(self.job, self.detail, fetched_via="direct_api", now=now)

            cached = cache.lookup(self.job, now=now + timedelta(days=1))
            self.assertEqual(123, cached["recruitNo"])
            self.assertNotIn("phoneNumber", cached)
            self.assertNotIn("managerEmail", cached)

            changed = {**self.job, "pay": "16,000원"}
            self.assertIsNone(cache.lookup(changed, now=now + timedelta(days=1)))

            cache.mark_classification(123, "eligible")
            self.assertTrue(cache.requires_live_refresh(123))
            self.assertIsNone(
                cache.lookup(
                    self.job,
                    force_refresh=cache.requires_live_refresh(123),
                    now=now + timedelta(days=1),
                )
            )

    def test_excluded_cache_entry_expires_and_atomic_file_reloads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "details.json"
            cache = DetailCache(path, excluded_ttl_days=7)
            now = datetime(2026, 8, 1, tzinfo=timezone.utc)
            cache.store(self.job, self.detail, fetched_via="direct_api", now=now)
            cache.mark_classification(123, "excluded")
            cache.save()

            loaded = DetailCache.load(path, excluded_ttl_days=7)
            self.assertIsNone(
                loaded.lookup(self.job, now=now + timedelta(days=8))
            )
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_live_refresh_uses_closing_actionable_and_applied_tiers(self):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 8, 9, 10, tzinfo=timezone.utc)

            eligible = DetailCache(Path(directory) / "eligible.json")
            eligible.store(self.job, self.detail, fetched_via="direct_api", now=now)
            eligible.mark_classification(123, "eligible")
            self.assertFalse(
                eligible.requires_live_refresh(
                    self.job, now=now + timedelta(hours=11)
                )
            )
            self.assertEqual(
                "eligible",
                eligible.live_refresh_reason(
                    self.job, now=now + timedelta(hours=12)
                ),
            )

            closing_job = {
                **self.job,
                "closingDate": (now + timedelta(hours=48)).isoformat(),
            }
            closing = DetailCache(Path(directory) / "closing.json")
            closing.store(
                closing_job, self.detail, fetched_via="direct_api", now=now
            )
            closing.mark_classification(123, "eligible")
            self.assertEqual(
                "closing",
                closing.live_refresh_reason(
                    closing_job, now=now + timedelta(hours=4)
                ),
            )

            applied = DetailCache(Path(directory) / "applied.json")
            applied.store(self.job, self.detail, fetched_via="direct_api", now=now)
            applied.mark_classification(123, "applied")
            self.assertFalse(
                applied.requires_live_refresh(
                    self.job, now=now + timedelta(hours=23)
                )
            )
            self.assertEqual(
                "applied",
                applied.live_refresh_reason(
                    self.job, now=now + timedelta(hours=24)
                ),
            )

    def test_privacy_sanitizer_preserves_job_content(self):
        result = sanitize_detail_for_cache(self.detail)

        self.assertEqual("근무요일: 토요일, 일요일", result["content"])
        self.assertNotIn("phoneNumber", result)

    def test_prune_drops_inactive_excluded_but_retains_recent_eligible(self):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 8, 12, tzinfo=timezone.utc)
            cache = DetailCache(Path(directory) / "details.json", retention_days=7)
            cache.store(self.job, self.detail, fetched_via="direct_api", now=now)
            cache.mark_classification(123, "excluded")
            eligible_job = {**self.job, "recruitNo": 456}
            cache.store(
                eligible_job,
                {**self.detail, "recruitNo": 456},
                fetched_via="direct_api",
                now=now,
            )
            cache.mark_classification(456, "eligible")

            removed = cache.prune(set(), now=now)

            self.assertEqual(1, removed)
            self.assertNotIn("123", cache.entries)
            self.assertIn("456", cache.entries)

    def test_closed_entries_are_removed_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = DetailCache(Path(directory) / "details.json")
            cache.store(self.job, self.detail, fetched_via="direct_api")

            removed = cache.remove_many({123}, reason="closed")

            self.assertEqual(1, removed)
            self.assertEqual(1, cache.metrics()["removed_closed_entries"])


if __name__ == "__main__":
    unittest.main()
