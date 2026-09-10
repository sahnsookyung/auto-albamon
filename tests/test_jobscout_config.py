from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from tools.jobscout_config import (
    BASE_CONFIG_PATH,
    CONFIG_ENV_NAME,
    ConfigurationError,
    get_settings,
    load_applicant_profile,
    load_settings,
)


class JobScoutConfigurationTests(unittest.TestCase):
    def test_public_configuration_is_valid_and_contains_no_saved_search(self):
        settings = load_settings(base_path=BASE_CONFIG_PATH)

        self.assertEqual(0, settings.albamon.saved_search_id)
        self.assertLess(settings.albamon.availability_end_date, date.today())
        self.assertLess(settings.weekend_agencies.search_end, date.today())
        self.assertEqual(900, settings.kakao.daily_limit)
        self.assertIn("huplux", settings.weekend_agencies.enabled_sources)
        self.assertEqual(
            set(settings.albamon.evening.stations),
            set(settings.albamon.evening.station_coordinates),
        )

    def test_local_override_merges_nested_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            override = Path(directory) / "override.toml"
            override.write_text(
                """
[albamon]
saved_search_id = 42

[albamon.evening]
available_from = "20:00"

[commute]
base_limit_minutes = 35
""".strip(),
                encoding="utf-8",
            )

            settings = load_settings(
                base_path=BASE_CONFIG_PATH,
                override_path=override,
            )

        self.assertEqual(42, settings.albamon.saved_search_id)
        self.assertEqual("20:00", settings.albamon.evening.available_from)
        self.assertEqual(35, settings.commute.base_limit_minutes)
        self.assertEqual(900, settings.kakao.daily_limit)

    def test_unknown_option_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            override = Path(directory) / "override.toml"
            override.write_text("[albamon]\nworkerz = 4\n", encoding="utf-8")

            with self.assertRaisesRegex(ConfigurationError, "unknown options"):
                load_settings(base_path=BASE_CONFIG_PATH, override_path=override)

    def test_station_catalog_rejects_incomplete_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            override = Path(directory) / "override.toml"
            override.write_text(
                "[albamon.evening]\nstations = [{ name = '새역' }]\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigurationError, "contain exactly"):
                load_settings(base_path=BASE_CONFIG_PATH, override_path=override)

    def test_secret_like_option_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            override = Path(directory) / "override.toml"
            override.write_text(
                "[kakao]\napi_key = 'must-not-live-here'\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigurationError, "forbidden"):
                load_settings(base_path=BASE_CONFIG_PATH, override_path=override)

    def test_credential_like_value_is_rejected_even_under_allowed_key(self):
        with tempfile.TemporaryDirectory() as directory:
            override = Path(directory) / "override.toml"
            override.write_text(
                "[commute]\ndefault_origin_name = 'KakaoAK abcdefghijklmnop'\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigurationError, "credential-like value"):
                load_settings(base_path=BASE_CONFIG_PATH, override_path=override)

    def test_explicit_missing_override_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.toml"
            with patch.dict("os.environ", {CONFIG_ENV_NAME: str(missing)}, clear=False):
                get_settings.cache_clear()
                try:
                    with self.assertRaisesRegex(
                        ConfigurationError, "override does not exist"
                    ):
                        get_settings()
                finally:
                    get_settings.cache_clear()

    def test_direct_missing_override_fails_closed(self):
        with self.assertRaisesRegex(ConfigurationError, "override does not exist"):
            load_settings(
                base_path=BASE_CONFIG_PATH,
                override_path=Path("/definitely/missing/jobscout.toml"),
            )

    def test_missing_applicant_profile_is_an_empty_fact_set(self):
        self.assertEqual(
            {},
            load_applicant_profile(Path("/definitely/missing/applicant-profile.toml")),
        )

    def test_applicant_profile_rejects_contact_data(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile.toml"
            profile.write_text(
                """
[facts.contact]
fact = "contact me at person@example.com"
source = "synthetic test fixture"
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigurationError, "contact data"):
                load_applicant_profile(profile)

    def test_applicant_profile_rejects_identity_fact_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile.toml"
            profile.write_text(
                "[facts.home_address]\nfact = 'somewhere'\nsource = 'synthetic test fixture'\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigurationError, "identity data"):
                load_applicant_profile(profile)

    def test_applicant_profile_loads_only_verified_fact_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile.toml"
            profile.write_text(
                """
[facts.customer_service]
fact = "고객 응대 경험"
source = "user verified"
""".strip(),
                encoding="utf-8",
            )

            result = load_applicant_profile(profile)

        self.assertEqual("고객 응대 경험", result["customer_service"]["fact"])


if __name__ == "__main__":
    unittest.main()
