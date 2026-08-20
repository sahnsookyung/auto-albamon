export function albamonPointForJob(job) {
  const lanes = new Set(job?.eligibility_lanes || []);
  const remote = job?.remote_job === true || lanes.has("remote_short_term");
  return {
    job: job || {},
    mode: remote ? "remote" : "onsite",
    commute: remote ? 0 : numericValue(job?.transit_minutes),
    pay: numericValue(job?.comparable_hourly_pay),
    frontier: job?.pareto_frontier === true,
  };
}

export function albamonParetoPoints(jobs) {
  const points = [];
  for (const job of jobs || []) {
    if (job?.application_disposition !== "apply") continue;
    if (job?.pay_comparison_reliable === false) continue;
    const point = albamonPointForJob(job);
    if (!Number.isFinite(point.pay) || point.pay <= 0) continue;
    const { commute } = point;
    const remote = point.mode === "remote";
    if (!remote && (job.commute_policy_eligible !== true || !Number.isFinite(commute) || commute < 0)) {
      continue;
    }
    points.push(point);
  }
  return points.sort((left, right) =>
    left.mode.localeCompare(right.mode) ||
    left.commute - right.commute ||
    right.pay - left.pay ||
    Number(left.job.recruit_no) - Number(right.job.recruit_no)
  );
}

export function albamonFrontierGroups(points) {
  const groups = new Map();
  for (const point of points || []) {
    if (!point.frontier) continue;
    if (!groups.has(point.mode)) groups.set(point.mode, []);
    groups.get(point.mode).push(point);
  }
  for (const group of groups.values()) {
    group.sort((left, right) => left.commute - right.commute || left.pay - right.pay);
  }
  return groups;
}

function compactText(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function numericValue(value) {
  return value === null || value === undefined || value === "" ? Number.NaN : Number(value);
}

export function albamonPointSummary(point) {
  const job = point?.job || {};
  const shift = [
    ...(job.late_night_shift_options || []),
    ...(job.target_date_options || []),
    ...(job.weekend_shift_options || []),
  ].map(compactText).find(Boolean) || compactText(job.work_time_option || job.work_time);
  const schedule = [compactText(job.work_days), shift].filter(Boolean).join(" · ");
  return {
    recruitNo: Number(job.recruit_no),
    company: compactText(job.company) || "Unknown company",
    title: compactText(job.title) || "Untitled job",
    area: compactText(job.target_district || job.area) || "Area unknown",
    pay: Number(point?.pay) || 0,
    commuteLabel: point?.mode === "remote"
      ? "Remote"
      : Number.isFinite(point?.commute) ? `${point.commute} min` : "Unverified",
    schedule: schedule || "Schedule not stated",
    frontier: point?.frontier === true,
  };
}
