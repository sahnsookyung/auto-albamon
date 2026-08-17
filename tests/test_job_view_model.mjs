import assert from "node:assert/strict";
import test from "node:test";

import {
  buildJobViewModels,
  filterAndSortJobModels,
} from "../web/job-view-model.mjs";

function job(recruitNo, overrides = {}) {
  return {
    recruit_no: recruitNo,
    company: "테스트",
    title: `Job ${recruitNo}`,
    application_disposition: "apply",
    comparable_hourly_pay: 12000,
    transit_minutes: 20,
    commute_policy_eligible: true,
    utility_rank: recruitNo,
    target_district: "송파구",
    search_zone: "core",
    eligibility_lanes: ["weekend_short_term"],
    application_methods: ["online"],
    hard_requirements: [],
    ...overrides,
  };
}

function criteria(overrides = {}) {
  return {
    query: "",
    status: "apply",
    schedule: "all",
    commutePolicyOnly: true,
    maxCommute: null,
    minPay: 0,
    district: "all",
    zone: "all",
    paretoOnly: false,
    newOnly: false,
    closingOnly: false,
    requirementsOnly: false,
    easyApplyOnly: false,
    sort: "rank",
    ...overrides,
  };
}

test("precomputed models support compound filters", () => {
  const jobs = [
    job(1, { title: "잠실 주말", pareto_frontier: true }),
    job(2, { title: "강남 주말", comparable_hourly_pay: 18000 }),
    job(3, { title: "잠실 평일", application_disposition: "do_not_apply" }),
  ];
  const models = buildJobViewModels(jobs);

  const filtered = filterAndSortJobModels(
    models,
    criteria({ query: "잠실", paretoOnly: true }),
  );

  assert.deepEqual(filtered.map((item) => item.recruit_no), [1]);
});

test("pay and date sorting reuse normalized numeric values", () => {
  const jobs = [
    job(1, { comparable_hourly_pay: 15000, first_seen_at: "2026-08-15T10:00:00+09:00" }),
    job(2, { comparable_hourly_pay: 18000, first_seen_at: "2026-08-14T10:00:00+09:00" }),
  ];
  const models = buildJobViewModels(jobs);

  assert.deepEqual(
    filterAndSortJobModels(models, criteria({ sort: "pay" })).map((item) => item.recruit_no),
    [2, 1],
  );
  assert.deepEqual(
    filterAndSortJobModels(models, criteria({ sort: "new" })).map((item) => item.recruit_no),
    [1, 2],
  );
});

test("shortlist filtering and easy-apply checks stay independent", () => {
  const jobs = [
    job(1),
    job(2, { application_methods: ["phone"] }),
    job(3),
  ];
  const models = buildJobViewModels(jobs);

  const filtered = filterAndSortJobModels(
    models,
    criteria({ easyApplyOnly: true }),
    new Set([1, 2]),
    true,
  );

  assert.deepEqual(filtered.map((item) => item.recruit_no), [1]);
});

test("needs-review status shows only jobs with an explicit review reason", () => {
  const jobs = [
    job(1, { needs_review_reason: "verify_requirements" }),
    job(2, { application_disposition: "maybe_later", needs_review_reason: "deferred" }),
    job(3, { application_disposition: "do_not_apply", needs_review_reason: "" }),
  ];

  const filtered = filterAndSortJobModels(
    buildJobViewModels(jobs),
    criteria({ status: "needs_review", commutePolicyOnly: false }),
  );

  assert.deepEqual(filtered.map((item) => item.recruit_no), [1, 2]);
});

test("requirement-review status excludes deferred decisions", () => {
  const jobs = [
    job(1, { needs_review_reason: "verify_requirements" }),
    job(2, { application_disposition: "maybe_later", needs_review_reason: "deferred" }),
  ];

  const filtered = filterAndSortJobModels(
    buildJobViewModels(jobs),
    criteria({ status: "review_requirements", commutePolicyOnly: false }),
  );

  assert.deepEqual(filtered.map((item) => item.recruit_no), [1]);
});
