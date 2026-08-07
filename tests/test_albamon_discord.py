import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.albamon_discord import (
    actionable_jobs,
    format_job_message,
    main,
    notification_identity,
    notify_new_jobs,
    post_discord_message,
    resolve_discord_webhook,
    validate_report_pair,
    validate_discord_webhook_url,
)


def combined_job(recruit_no: int, *, title: str = "주말 홀 아르바이트") -> dict:
    return {
        "recruit_no": recruit_no,
        "title": title,
        "company": "테스트 식당",
        "working_address": "서울 송파구 올림픽로 1",
        "work_time": "10:00~18:00",
        "salary": "13,000원",
    }


def unified_job(recruit_no: int, *, disposition: str = "apply") -> dict:
    return {
        "recruit_no": recruit_no,
        "application_disposition": disposition,
        "utility_rank": 1,
        "company": "테스트 식당",
        "title": "주말 홀 아르바이트",
        "url": f"https://www.albamon.com/jobs/detail/{recruit_no}",
        "salary_type": "시급",
        "salary": "13,000원",
        "comparable_hourly_pay": 13_000,
        "area": "서울 송파구 올림픽로 1",
        "work_days": "주말(토, 일)",
        "work_time": "10:00~18:00",
        "transit_minutes": 18,
        "transit_transfers": 0,
        "duration_requirement": "No one-month minimum found",
        "availability_end_date": "2026-09-09",
        "hard_requirements": ["Health certificate required"],
        "application_methods": ["online", "easy_sms"],
        "message": "안녕하세요. [REMOVED_PRIVATE_PROFILE_7f43392d5e]과 보건증이 있습니다. 책임감 있게 일하겠습니다.",
        "verify_before_sending": ["토·일 모두 근무 가능한지", "시급 적용 여부"],
    }


class AlbamonDiscordTests(unittest.TestCase):
    def test_remote_notification_says_no_commute(self):
        record = {
            **unified_job(1),
            "remote_job": True,
            "eligibility_lanes": ["remote_short_term"],
            "transit_route_status": "not_required",
            "transit_minutes": 0,
        }

        message = format_job_message(record, position=1, total=1)

        self.assertIn("재택근무 · 통근 없음", message)
        self.assertNotIn("잠실동 189-4 0분", message)

    def test_report_pair_requires_matching_generation_and_job_ids(self):
        combined = {
            "run_id": "run-1",
            "generated_at": "2026-08-12T02:00:00+09:00",
            "jobs": [combined_job(1)],
            "logistics_jobs": [],
        }
        unified = {
            "source_run_id": "run-1",
            "source_generated_at": "2026-08-12T02:00:00+09:00",
            "jobs": [unified_job(1)],
        }

        validate_report_pair(combined, unified)
        unified["source_run_id"] = "run-2"
        with self.assertRaisesRegex(ValueError, "run IDs"):
            validate_report_pair(combined, unified)

    def test_report_pair_rejects_stale_unified_jobs(self):
        with self.assertRaisesRegex(ValueError, "different job generations"):
            validate_report_pair(
                {"jobs": [combined_job(1)], "logistics_jobs": []},
                {"jobs": [unified_job(2)]},
            )

    def test_repost_with_new_recruit_number_keeps_semantic_identity(self):
        self.assertEqual(
            notification_identity(combined_job(1)),
            notification_identity(combined_job(2)),
        )

    def test_notification_identity_preserves_material_schedule_and_pay_variants(self):
        old = combined_job(1)
        old.update({"work_days": "토요일", "salary": "13,000원"})
        repost = combined_job(2)
        repost.update({"work_days": "일요일", "salary": "15,000원"})

        self.assertNotEqual(notification_identity(old), notification_identity(repost))

    def test_notification_identity_stays_stable_for_unchanged_repost(self):
        old = combined_job(1)
        old.update({"work_days": "토요일", "salary": "13,000원"})
        repost = combined_job(2)
        repost.update({"work_days": "토요일", "salary": "13,000원"})

        self.assertEqual(notification_identity(old), notification_identity(repost))

    def test_only_actionable_jobs_are_joined_and_sorted(self):
        combined = {"jobs": [combined_job(1), combined_job(2)], "logistics_jobs": []}
        first = unified_job(1)
        first["utility_rank"] = 2
        second = unified_job(2, disposition="already_applied")
        result = actionable_jobs(combined, {"jobs": [second, first]})

        self.assertEqual([1], [record["recruit_no"] for _, record in result])

    def test_bootstrap_records_existing_jobs_without_sending(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            sender_calls: list[str] = []
            metrics = notify_new_jobs(
                {"jobs": [combined_job(1)], "logistics_jobs": []},
                {"jobs": [unified_job(1)]},
                state_path=state_path,
                sender=sender_calls.append,
            )

            self.assertTrue(metrics["bootstrapped"])
            self.assertEqual([], sender_calls)
            jobs_state = json.loads(state_path.read_text())["jobs"]
            self.assertEqual(1, len(jobs_state))
            self.assertEqual("", next(iter(jobs_state.values()))["notified_at"])

    def test_new_job_is_sent_once_and_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            first_combined = {"jobs": [combined_job(1)], "logistics_jobs": []}
            first_unified = {"jobs": [unified_job(1)]}
            notify_new_jobs(
                first_combined,
                first_unified,
                state_path=state_path,
                sender=lambda _message: None,
            )

            second_job = combined_job(2, title="새 주말 행사 스태프")
            second_record = unified_job(2)
            second_record["title"] = second_job["title"]
            combined = {"jobs": [combined_job(1), second_job], "logistics_jobs": []}
            unified = {"jobs": [unified_job(1), second_record]}
            sent: list[str] = []

            first_metrics = notify_new_jobs(
                combined,
                unified,
                state_path=state_path,
                sender=sent.append,
            )
            second_metrics = notify_new_jobs(
                combined,
                unified,
                state_path=state_path,
                sender=sent.append,
            )

            self.assertEqual(1, first_metrics["sent_jobs"])
            self.assertEqual(0, second_metrics["sent_jobs"])
            self.assertEqual(1, len(sent))
            self.assertIn("새 주말 행사 스태프", sent[0])

    def test_changed_details_on_same_recruit_are_not_a_new_job(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            notify_new_jobs(
                {"jobs": [combined_job(1)], "logistics_jobs": []},
                {"jobs": [unified_job(1)]},
                state_path=state_path,
                sender=lambda _message: None,
            )
            changed_job = combined_job(1)
            changed_job["work_time"] = "12:00~20:00"
            sent: list[str] = []

            metrics = notify_new_jobs(
                {"jobs": [changed_job], "logistics_jobs": []},
                {"jobs": [unified_job(1)]},
                state_path=state_path,
                sender=sent.append,
            )

            self.assertEqual(0, metrics["new_jobs"])
            self.assertEqual([], sent)
            self.assertEqual(1, len(json.loads(state_path.read_text())["jobs"]))

    def test_failed_send_does_not_mark_job_as_notified(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            notify_new_jobs(
                {"jobs": [combined_job(1)], "logistics_jobs": []},
                {"jobs": [unified_job(1)]},
                state_path=state_path,
                sender=lambda _message: None,
            )
            new_job = combined_job(2, title="새 행사")
            new_record = unified_job(2)
            new_record["title"] = "새 행사"

            with self.assertRaises(RuntimeError):
                notify_new_jobs(
                    {"jobs": [combined_job(1), new_job], "logistics_jobs": []},
                    {"jobs": [unified_job(1), new_record]},
                    state_path=state_path,
                    sender=lambda _message: (_ for _ in ()).throw(RuntimeError("failed")),
                )

            self.assertEqual(1, len(json.loads(state_path.read_text())["jobs"]))

    def test_message_is_bounded_and_mentions_are_neutralized(self):
        record = unified_job(1)
        record["title"] = "@everyone " + ("긴 제목 " * 200)
        record["message"] = "지원 메시지 " * 300

        message = format_job_message(record, position=1, total=1)

        self.assertLessEqual(len(message), 2_000)
        self.assertNotIn("@everyone", message)
        self.assertIn(record["url"], message)
        self.assertIn("2026-09-09", message)

    def test_late_night_notification_is_visibly_labeled(self):
        record = unified_job(1)
        record.update(
            {
                "eligibility_lanes": ["songpa_late_night"],
                "late_night_shift_options": ["20:00~02:00 — structured work time"],
                "evening_nearest_station": "삼성역",
                "evening_station_walk_minutes": 8,
                "evening_station_distance_km": 0.48,
                "late_night_shift_journeys": [
                    {
                        "shift": "20:00~02:00",
                        "outbound": {"minutes": 22},
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
            }
        )

        message = format_job_message(record, position=1, total=1)

        self.assertIn("역삼·강남·송파·잠실·잠실새내·삼성 저녁", message)
        self.assertIn("19:00~02:00", message)
        self.assertIn("역세권 도보 추정 20분 이하", message)
        self.assertIn("삼성역 도보권 약 8분", message)
        self.assertIn("20:00~02:00", message)
        self.assertIn("N31", message)
        self.assertIn("자전거 예비 14분", message)

    def test_temporary_single_day_notification_is_visibly_labeled(self):
        record = unified_job(1)
        record.update(
            {
                "eligibility_lanes": ["aug_15_17_single_day"],
                "target_date_options": ["2026-08-17"],
            }
        )

        message = format_job_message(record, position=1, total=1)

        self.assertIn("8/15·8/17", message)
        self.assertIn("2026-08-17", message)

    def test_late_night_notification_shows_naver_future_elapsed_time(self):
        record = unified_job(1)
        record.update(
            {
                "eligibility_lanes": ["songpa_late_night"],
                "late_night_shift_options": ["20:00~02:00 — exact"],
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
                ],
            }
        )

        message = format_job_message(record, position=1, total=1)

        self.assertIn("Naver(08-15 19:30→08-15 19:58)", message)
        self.assertIn("Naver N31 38분", message)

    def test_webhook_is_loaded_without_evaluating_env_file(self):
        webhook = "https://discord.com/api/webhooks/123/token"
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "OTHER=$(not-evaluated)\nDISCORD_WEBHOOK_URL='" + webhook + "'\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True):
                self.assertEqual(webhook, resolve_discord_webhook(env_file))

    def test_explicit_env_file_wins_over_inherited_webhook(self):
        workspace_webhook = "https://discord.com/api/webhooks/123/workspace"
        inherited_webhook = "https://discord.com/api/webhooks/456/inherited"
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                f"DISCORD_WEBHOOK_URL={workspace_webhook}\n",
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {"DISCORD_WEBHOOK_URL": inherited_webhook},
                clear=True,
            ):
                self.assertEqual(workspace_webhook, resolve_discord_webhook(env_file))

    def test_webhook_validation_rejects_non_discord_hosts(self):
        with self.assertRaises(ValueError):
            validate_discord_webhook_url("https://example.com/api/webhooks/123/token")

    @patch("tools.albamon_discord.urllib.request.urlopen")
    def test_webhook_payload_disables_mentions(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.status = 204
        response.read.return_value = b""

        post_discord_message(
            "https://discord.com/api/webhooks/123/token", "hello @everyone"
        )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual({"parse": []}, payload["allowed_mentions"])
        self.assertEqual("hello @everyone", payload["content"])

    @patch("tools.albamon_discord.post_discord_message")
    def test_cli_can_bootstrap_and_announce(self, post_message):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            combined_path = root / "combined.json"
            unified_path = root / "unified.json"
            env_path = root / ".env"
            state_path = root / "state.json"
            combined_path.write_text(
                json.dumps({"jobs": [combined_job(1)], "logistics_jobs": []}),
                encoding="utf-8",
            )
            unified_path.write_text(
                json.dumps({"jobs": [unified_job(1)]}), encoding="utf-8"
            )
            env_path.write_text(
                "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123/token\n",
                encoding="utf-8",
            )

            result = main(
                [
                    "--combined",
                    str(combined_path),
                    "--report",
                    str(unified_path),
                    "--state",
                    str(state_path),
                    "--webhook-env-file",
                    str(env_path),
                    "--bootstrap",
                    "--announce-bootstrap",
                ]
            )

        self.assertEqual(0, result)
        post_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
