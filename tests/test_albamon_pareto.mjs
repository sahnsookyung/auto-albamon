import assert from "node:assert/strict";
import test from "node:test";

import {
  albamonFrontierGroups,
  albamonParetoPoints,
  albamonPointForJob,
  albamonPointSummary,
} from "../web/albamon-pareto.mjs";

const job = (recruitNo, overrides = {}) => ({
  recruit_no: recruitNo,
  application_disposition: "apply",
  comparable_hourly_pay: 15_000,
  transit_minutes: 20,
  commute_policy_eligible: true,
  pareto_frontier: false,
  ...overrides,
});

test("chart points apply candidate rules and recompute the selected frontier", () => {
  const points = albamonParetoPoints([
    job(1, { pareto_frontier: true }),
    job(2, { application_disposition: "do_not_apply" }),
    job(3, { commute_policy_eligible: false }),
    job(4, { comparable_hourly_pay: 0 }),
    job(6, { comparable_hourly_pay: 50_000, pay_comparison_reliable: false }),
    job(5, {
      remote_job: true,
      transit_minutes: null,
      commute_policy_eligible: false,
      pareto_frontier: true,
    }),
    job(7, { transit_minutes: 30, comparable_hourly_pay: 12_000, pareto_frontier: true }),
  ]);

  assert.deepEqual(points.map((point) => point.job.recruit_no), [1, 7, 5]);
  assert.equal(points.find((point) => point.job.recruit_no === 7).frontier, false);
  assert.equal(points.at(-1).commute, 0);
});

test("shift-total mode can change the frontier and excludes unknown totals", () => {
  const input = [
    job(1, { transit_minutes: 20, comparable_hourly_pay: 30_000, estimated_shift_earnings_won: 60_000 }),
    job(2, { transit_minutes: 30, comparable_hourly_pay: 20_000, estimated_shift_earnings_won: 160_000 }),
    job(3, { transit_minutes: 10, comparable_hourly_pay: 15_000, estimated_shift_earnings_won: null }),
  ];

  assert.deepEqual(albamonParetoPoints(input, "hourly").filter((point) => point.frontier).map((point) => point.job.recruit_no), [3, 1]);
  assert.deepEqual(albamonParetoPoints(input, "shift_total").filter((point) => point.frontier).map((point) => point.job.recruit_no), [1, 2]);
});

test("frontier lines remain separate for onsite and remote comparisons", () => {
  const points = albamonParetoPoints([
    job(1, { transit_minutes: 10, comparable_hourly_pay: 12_000, pareto_frontier: true }),
    job(2, { transit_minutes: 30, comparable_hourly_pay: 18_000, pareto_frontier: true }),
    job(3, { remote_job: true, comparable_hourly_pay: 20_000, pareto_frontier: true }),
  ]);
  const groups = albamonFrontierGroups(points);

  assert.deepEqual(groups.get("onsite").map((point) => point.job.recruit_no), [1, 2]);
  assert.deepEqual(groups.get("remote").map((point) => point.job.recruit_no), [3]);
});

test("interactive point summaries expose concise hover and accessibility details", () => {
  const [point] = albamonParetoPoints([
    job(9, {
      company: "  Example   Hotel ",
      title: "Weekend banquet helper",
      target_district: "강남구",
      work_days: "토요일",
      weekend_shift_options: ["10:00~18:00"],
      comparable_hourly_pay: 18_000,
      transit_minutes: 32,
      pareto_frontier: true,
    }),
  ]);

  assert.deepEqual(albamonPointSummary(point), {
    recruitNo: 9,
    company: "Example Hotel",
    title: "Weekend banquet helper",
    area: "강남구",
    pay: 18_000,
    payMetric: "hourly",
    commuteLabel: "32 min",
    schedule: "토요일 · 10:00~18:00",
    frontier: true,
  });
});

test("map point summaries preserve details outside the Pareto candidate set", () => {
  const point = albamonPointForJob(job(12, {
    application_disposition: "maybe_later",
    company: "Deferred hotel",
    title: "Evening banquet",
    area: "송파구",
    work_days: "금요일",
    work_time: "19:00~23:00",
    transit_minutes: null,
    commute_policy_eligible: false,
  }));

  assert.deepEqual(albamonPointSummary(point), {
    recruitNo: 12,
    company: "Deferred hotel",
    title: "Evening banquet",
    area: "송파구",
    pay: 15_000,
    payMetric: "hourly",
    commuteLabel: "Unverified",
    schedule: "금요일 · 19:00~23:00",
    frontier: false,
  });
});
