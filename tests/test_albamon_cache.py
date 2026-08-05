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

    def test_privacy_sanitizer_preserves_job_content(self):
        result = sanitize_detail_for_cache(self.detail)

        self.assertEqual("근무요일: 토요일, 일요일", result["content"])
        self.assertNotIn("phoneNumber", result)


if __name__ == "__main__":
    unittest.main()
