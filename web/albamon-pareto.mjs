export function albamonParetoPoints(jobs) {
  const points = [];
  for (const job of jobs || []) {
    if (job?.application_disposition !== "apply") continue;
    const pay = Number(job.comparable_hourly_pay);
    if (!Number.isFinite(pay) || pay <= 0) continue;

    const lanes = new Set(job.eligibility_lanes || []);
    const remote = job.remote_job === true || lanes.has("remote_short_term");
    const commute = remote ? 0 : Number(job.transit_minutes);
    if (!remote && (job.commute_policy_eligible !== true || !Number.isFinite(commute) || commute < 0)) {
      continue;
    }
    points.push({
      job,
      mode: remote ? "remote" : "onsite",
      commute,
      pay,
      frontier: job.pareto_frontier === true,
    });
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
