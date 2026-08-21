import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import albamon_combined_jobs as combined
from tools.albamon_refresh import (
    append_performance_history,
    main,
    parse_args,
    record_refresh_performance,
    validate_refresh_outputs,
)
from tools.albamon_runtime import exclusive_file_lock


class AlbamonRefreshTests(unittest.TestCase):
    def test_integrity_check_recalculates_expiry_instead_of_trusting_flag(self):
        report = {
            "run_id": "run-1",
            "generated_at": "2026-08-17T12:00:00+09:00",
            "availability_end_date": "2026-09-09",
            "eligible_count": 1,
            "jobs": [
                {
                    "recruit_no": 1,
                    "title": "8월15일 보조연기자 모집 #당일알바",
                    "work_period": "1주일이하",
                    "work_term_ended": False,
                    "availability_compatible": True,
                    "availability_end_date": "2026-09-09",
                }
            ],
            "logistics_jobs": [],
        }
        unified = {
            "source_run_id": "run-1",
            "source_generated_at": "2026-08-17T12:00:00+09:00",
            "jobs": [{"recruit_no": 1}],
        }

        with self.assertRaisesRegex(ValueError, "expired work term"):
            validate_refresh_outputs(
                report,
                {"jobs": {"1": {"recruit_no": 1}}},
                unified,
                observed_at=datetime(2026, 8, 17, 12, 0),
            )

    def test_integrity_check_requires_remote_no_commute_result(self):
        report = {
            "run_id": "run-1",
            "generated_at": "2026-08-12T02:00:00+09:00",
            "availability_end_date": "2026-09-09",
            "eligible_count": 1,
            "jobs": [
                {
                    "recruit_no": 1,
                    "work_term_ended": False,
                    "availability_compatible": True,
                    "availability_end_date": "2026-09-09",
                    "remote_job": True,
                    "eligibility_lanes": ["remote_short_term"],
                }
            ],
            "logistics_jobs": [],
        }
        unified = {
            "source_run_id": "run-1",
            "source_generated_at": "2026-08-12T02:00:00+09:00",
            "jobs": [{"recruit_no": 1}],
        }

        with self.assertRaisesRegex(ValueError, "Remote job"):
            validate_refresh_outputs(
                report,
                {
                    "jobs": {
                        "1": {
                            "route_status": "verified",
                            "transit_minutes": 1,
                            "distance_excluded": False,
                        }
                    }
                },
                unified,
            )

    def test_integrity_check_rejects_mixed_transit_generation(self):
        report = {
            "run_id": "run-1",
            "generated_at": "2026-08-12T02:00:00+09:00",
            "availability_end_date": "2026-09-09",
            "eligible_count": 1,
            "jobs": [
                {
                    "recruit_no": 1,
                    "work_term_ended": False,
                    "availability_compatible": True,
                    "availability_end_date": "2026-09-09",
                }
            ],
            "logistics_jobs": [],
        }
        unified = {
            "source_run_id": "run-1",
            "source_generated_at": "2026-08-12T02:00:00+09:00",
            "jobs": [{"recruit_no": 1}],
        }

        with self.assertRaisesRegex(ValueError, "Transit cache"):
            validate_refresh_outputs(
                report,
                {"jobs": {"2": {"recruit_no": 2}}},
                unified,
            )

    def test_notify_mode_is_available_to_the_single_refresh_command(self):
        with patch("sys.argv", ["albamon_refresh.py", "--notify"]):
            args = parse_args()

        self.assertTrue(args.notify)

    def test_default_availability_deadline_comes_from_configuration(self):
        with patch("sys.argv", ["albamon_refresh.py"]):
            args = parse_args()

        self.assertEqual(combined.DEFAULT_AVAILABILITY_END_DATE, args.availability_end_date)

    def test_overlapping_refresh_exits_cleanly_without_running_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "refresh.lock"
            with exclusive_file_lock(lock_path), patch(
                "sys.argv",
                ["albamon_refresh.py", "--lock-file", str(lock_path)],
            ), patch("tools.albamon_refresh.run_refresh") as run_refresh:
                result = main()

        self.assertEqual(0, result)
        run_refresh.assert_not_called()

    def test_late_night_lane_can_be_excluded(self):
        with patch("sys.argv", ["albamon_refresh.py", "--exclude-late-night"]):
            args = parse_args()

        self.assertTrue(args.exclude_late_night)

    def test_performance_history_is_atomic_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "performance.json"
            append_performance_history(path, {"run": 1}, limit=2)
            append_performance_history(path, {"run": 2}, limit=2)
            append_performance_history(path, {"run": 3}, limit=2)

            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual([2, 3], [entry["run"] for entry in payload["runs"]])
        self.assertEqual(2, payload["limit"])
        self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_performance_history_recovers_from_invalid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "performance.json"
            path.write_text("not json", encoding="utf-8")

            append_performance_history(path, {"run": 1}, limit=50)
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual([{"run": 1}], payload["runs"])

    def test_performance_history_failure_does_not_fail_refresh(self):
        args = SimpleNamespace(
            performance_history=Path("unwritable/performance.json"),
            performance_history_limit=50,
        )
        with patch(
            "tools.albamon_refresh.append_performance_history",
            side_effect=OSError("read-only filesystem"),
        ):
            saved = record_refresh_performance(
                args,
                {"performance": {}},
                {"total_seconds": 1.0},
                {"eligible_jobs": 2},
            )

        self.assertFalse(saved)


if __name__ == "__main__":
    unittest.main()
