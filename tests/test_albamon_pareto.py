import unittest

from tools.albamon_pareto import annotate_pareto_frontier, pareto_frontier_ids


def job(recruit_no: int, pay: int, commute: int | None, **overrides) -> dict:
    record = {
        "recruit_no": recruit_no,
        "application_disposition": "apply",
        "comparable_hourly_pay": pay,
        "transit_minutes": commute,
        "commute_policy_eligible": True,
    }
    record.update(overrides)
    return record


class AlbamonParetoTests(unittest.TestCase):
    def test_frontier_keeps_real_pay_commute_tradeoffs(self):
        records = [
            job(1, 12_000, 10),
            job(2, 15_000, 20),
            job(3, 14_000, 25),
            job(4, 18_000, 40),
        ]

        self.assertEqual({1, 2, 4}, pareto_frontier_ids(records))

    def test_equal_metrics_are_both_non_dominated(self):
        records = [job(1, 15_000, 20), job(2, 15_000, 20), job(3, 14_000, 20)]

        self.assertEqual({1, 2}, pareto_frontier_ids(records))

    def test_non_actionable_or_unverified_jobs_are_not_candidates(self):
        records = [
            job(1, 20_000, 10, application_disposition="do_not_apply"),
            job(2, 19_000, None, commute_policy_eligible=False),
            job(3, 15_000, 20),
        ]

        self.assertEqual({3}, pareto_frontier_ids(records))

    def test_remote_and_onsite_frontiers_are_independent(self):
        records = [
            job(
                1,
                30_000,
                0,
                remote_job=True,
                eligibility_lanes=["remote_short_term"],
            ),
            job(2, 18_000, 30),
            job(3, 15_000, 40),
        ]

        self.assertEqual({1, 2}, pareto_frontier_ids(records))

    def test_annotation_marks_every_record_explicitly(self):
        records = [job(1, 15_000, 20), job(2, 14_000, 30)]

        count = annotate_pareto_frontier(records)

        self.assertEqual(1, count)
        self.assertTrue(records[0]["pareto_frontier"])
        self.assertFalse(records[1]["pareto_frontier"])


if __name__ == "__main__":
    unittest.main()
