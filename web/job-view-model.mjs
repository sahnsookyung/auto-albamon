"use strict";

const FAR_FUTURE = Number.MAX_SAFE_INTEGER;
const EASY_APPLICATION_METHODS = new Set(["online", "easy_sms"]);
const ZONE_SORT_ORDER = {
  remote: 0,
  core: 1,
  seocho_corridor: 2,
  east_near_core: 3,
  premium_stretch: 4,
  outer_seoul: 5,
};

function cleanText(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function numeric(value, fallback) {
  if (value === null || value === undefined || value === "") return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function timestamp(value, fallback) {
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function buildJobViewModels(jobs) {
  return jobs.map((job) => {
    const lanes = new Set(job.eligibility_lanes || []);
    return {
      job,
      id: Number(job.recruit_no),
      searchText: [
        job.company,
        job.title,
        job.area,
        job.working_address,
        job.logistics_category,
        job.search_zone_label,
        ...(job.target_date_options || []),
        ...(job.weekend_shift_options || []),
        ...(job.late_night_shift_options || []),
      ].map(cleanText).join(" ").toLocaleLowerCase("ko"),
      pay: numeric(job.comparable_hourly_pay, 0),
      commute: numeric(job.transit_minutes, FAR_FUTURE),
      rank: numeric(job.utility_rank, FAR_FUTURE),
      zoneRank: ZONE_SORT_ORDER[job.search_zone] ?? FAR_FUTURE,
      firstSeenAt: timestamp(job.first_seen_at, 0),
      closingAt: timestamp(job.closing_at, FAR_FUTURE),
      isRemoteLane: lanes.has("remote_short_term"),
      isLateNightLane: lanes.has("songpa_late_night"),
      isTargetDateLane: lanes.has("aug_15_17_single_day"),
      hardRequirementCount: (job.hard_requirements || []).length,
      hasEasyApplication: (job.application_methods || []).some((method) =>
        EASY_APPLICATION_METHODS.has(method)
      ),
    };
  });
}

function matchesSchedule(model, schedule) {
  if (schedule === "all") return true;
  if (schedule === "remote") return model.isRemoteLane;
  if (schedule === "late_night") return model.isLateNightLane;
  if (schedule === "target_date") return model.isTargetDateLane;
  return model.job.weekend_evidence_type === schedule;
}

function matches(model, criteria, comparedIds, shortlistOnly) {
  const { job } = model;
  if (criteria.query && !model.searchText.includes(criteria.query)) return false;
  if (criteria.status !== "all" && job.application_disposition !== criteria.status) return false;
  if (!matchesSchedule(model, criteria.schedule)) return false;
  if (
    criteria.commutePolicyOnly &&
    (model.commute === FAR_FUTURE || job.commute_policy_eligible !== true)
  ) return false;
  if (criteria.maxCommute !== null && model.commute >= criteria.maxCommute) return false;
  if (model.pay < criteria.minPay) return false;
  if (criteria.district !== "all" && job.target_district !== criteria.district) return false;
  if (criteria.zone !== "all" && job.search_zone !== criteria.zone) return false;
  if (criteria.paretoOnly && job.pareto_frontier !== true) return false;
  if (criteria.newOnly && !job.is_new) return false;
  if (criteria.closingOnly && !job.is_closing_soon) return false;
  if (criteria.requirementsOnly && model.hardRequirementCount > 0) return false;
  if (criteria.easyApplyOnly && !model.hasEasyApplication) return false;
  if (shortlistOnly && !comparedIds.has(model.id)) return false;
  return true;
}

function compareModels(left, right, sort) {
  let result;
  switch (sort) {
    case "new":
      result = right.firstSeenAt - left.firstSeenAt || left.rank - right.rank;
      break;
    case "pay":
      result = right.pay - left.pay || left.rank - right.rank;
      break;
    case "commute":
      result = left.commute - right.commute || left.rank - right.rank;
      break;
    case "closing":
      result = left.closingAt - right.closingAt || left.rank - right.rank;
      break;
    case "zone":
      result = left.zoneRank - right.zoneRank || left.rank - right.rank;
      break;
    default:
      result = left.rank - right.rank;
  }
  return result || left.id - right.id;
}

export function filterAndSortJobModels(
  models,
  criteria,
  comparedIds = new Set(),
  shortlistOnly = false,
) {
  const filtered = models.filter((model) =>
    matches(model, criteria, comparedIds, shortlistOnly)
  );
  filtered.sort((left, right) => compareModels(left, right, criteria.sort));
  return filtered.map((model) => model.job);
}
