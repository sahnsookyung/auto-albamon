import {
  hotelComparablePay,
  paretoHotelIds,
} from "./hotel-pareto.mjs";

function text(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function numeric(value) {
  if (value === "" || value === null || value === undefined) return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}

function scheduledShiftTotal(hourly, durationMinutes, advertisedTotal = null) {
  const advertised = numeric(advertisedTotal);
  if (advertised !== null && advertised > 0) return advertised;
  const rate = numeric(hourly);
  const duration = numeric(durationMinutes);
  if (rate === null || rate <= 0 || duration === null || duration <= 0) return null;
  return Math.round(rate * duration / 60);
}

function clockMinutes(value) {
  const match = /^(\d{2}):(\d{2})$/.exec(text(value));
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  return hour <= 23 && minute <= 59 ? hour * 60 + minute : null;
}

export function shiftBounds(value) {
  const [startText, endText] = text(value).split("~");
  const start = clockMinutes(startText);
  let end = clockMinutes(endText);
  if (start === null || end === null) return null;
  if (end <= start) end += 24 * 60;
  return { start, end };
}

function shiftDurationMinutes(value) {
  const bounds = shiftBounds(value);
  return bounds ? bounds.end - bounds.start : null;
}

export function shiftFitsWindow(shift, earliestStart = "", latestEnd = "") {
  const bounds = shiftBounds(shift);
  if (!bounds) return !earliestStart && !latestEnd;
  const startLimit = clockMinutes(earliestStart);
  let endLimit = clockMinutes(latestEnd);
  let { start, end } = bounds;
  if (startLimit !== null && endLimit !== null) {
    if (endLimit <= startLimit) endLimit += 24 * 60;
    if (start < startLimit && start < 6 * 60 && endLimit > 24 * 60) {
      start += 24 * 60;
      end += 24 * 60;
    }
    return start >= startLimit && end <= endLimit;
  }
  if (startLimit !== null && start < startLimit) return false;
  if (endLimit !== null) {
    const comparableEnd = end > 24 * 60 && endLimit < 6 * 60 ? endLimit + 24 * 60 : endLimit;
    if (end > comparableEnd) return false;
  }
  return true;
}

function normalizedOptions(hotel) {
  if ((hotel?.job_options || []).length) return hotel.job_options;
  return (hotel?.shift_options || []).flatMap((option) =>
    (option.dates || []).map((workDate) => ({
      option_id: `${workDate}|${option.shift}`,
      work_date: workDate,
      shift: option.shift,
      role_category: "other",
      actual_hourly_won: option.actual_hourly_max,
      weekly_allowance_hourly_won: option.weekly_allowance_hourly_max,
      best_agencies: option.best_agencies || [],
      agencies: option.best_agencies || [],
    }))
  );
}

function optionMatches(option, filters) {
  if (filters.dateStart && option.work_date < filters.dateStart) return false;
  if (filters.dateEnd && option.work_date > filters.dateEnd) return false;
  if (!shiftFitsWindow(option.shift, filters.shiftStart, filters.shiftEnd)) return false;
  if (filters.role && filters.role !== "all" && option.role_category !== filters.role) return false;
  if (filters.provider && filters.provider !== "all" && !(option.agencies || []).includes(filters.provider)) {
    return false;
  }
  return true;
}

function deriveHotel(hotel, options) {
  const actual = options.map((option) => numeric(option.actual_hourly_won)).filter((value) => value !== null);
  const weekly = options.map((option) => numeric(option.weekly_allowance_hourly_won)).filter((value) => value !== null);
  const adjusted = options.map((option) => numeric(option.commute_adjusted_hourly_won)).filter((value) => value !== null);
  const earnings = options.map((option) => numeric(option.estimated_shift_earnings_won)).filter((value) => value !== null);
  const actualTotals = options.map((option) => scheduledShiftTotal(
    option.actual_hourly_won,
    option.duration_minutes ?? shiftDurationMinutes(option.shift),
    option.estimated_shift_earnings_won,
  )).filter((value) => value !== null);
  const weeklyTotals = options.map((option) => scheduledShiftTotal(
    option.weekly_allowance_hourly_won,
    option.duration_minutes ?? shiftDurationMinutes(option.shift),
  )).filter((value) => value !== null);
  const dates = [...new Set(options.map((option) => option.work_date).filter(Boolean))].sort();
  const shifts = [...new Set(options.map((option) => option.shift).filter(Boolean))].sort();
  const providerLabels = new Map(
    (hotel.providers || []).map((provider) => [provider.agency, provider.label || provider.agency]),
  );
  const providerSummaries = new Map();
  for (const option of options) {
    const providerOffers = option.provider_offers?.length
      ? option.provider_offers
      : (option.agencies || []).map((agency) => ({
        agency,
        actual_hourly_won: option.actual_hourly_won,
        weekly_allowance_hourly_won: option.weekly_allowance_hourly_won,
        source_url: "",
      }));
    for (const offer of providerOffers) {
      if (!offer.agency) continue;
      const current = providerSummaries.get(offer.agency) || {
        agency: offer.agency,
        label: providerLabels.get(offer.agency) || offer.agency,
        actual_hourly_max: null,
        weekly_allowance_hourly_max: null,
        actual_shift_total_max: null,
        weekly_allowance_shift_total_max: null,
        representative_url: "",
        offer_count: 0,
      };
      const offerActual = numeric(offer.actual_hourly_won);
      const offerWeekly = numeric(offer.weekly_allowance_hourly_won);
      const durationMinutes = numeric(option.duration_minutes) ?? shiftDurationMinutes(option.shift);
      const offerActualTotal = scheduledShiftTotal(
        offerActual,
        durationMinutes,
        offer.advertised_shift_won ?? (
          (option.best_agencies || []).includes(offer.agency)
            ? option.estimated_shift_earnings_won
            : null
        ),
      );
      const offerWeeklyTotal = scheduledShiftTotal(offerWeekly, durationMinutes);
      if (offerActual !== null && (current.actual_hourly_max === null || offerActual > current.actual_hourly_max)) {
        current.actual_hourly_max = offerActual;
        current.representative_url = offer.source_url || current.representative_url;
      }
      if (offerWeekly !== null && (current.weekly_allowance_hourly_max === null || offerWeekly > current.weekly_allowance_hourly_max)) {
        current.weekly_allowance_hourly_max = offerWeekly;
      }
      if (offerActualTotal !== null && (current.actual_shift_total_max === null || offerActualTotal > current.actual_shift_total_max)) {
        current.actual_shift_total_max = offerActualTotal;
      }
      if (offerWeeklyTotal !== null && (current.weekly_allowance_shift_total_max === null || offerWeeklyTotal > current.weekly_allowance_shift_total_max)) {
        current.weekly_allowance_shift_total_max = offerWeeklyTotal;
      }
      current.offer_count += 1;
      providerSummaries.set(offer.agency, current);
    }
  }
  const providers = [...providerSummaries.values()].sort((left, right) =>
    (Number(right.actual_hourly_max) || 0) - (Number(left.actual_hourly_max) || 0) ||
    String(left.label).localeCompare(String(right.label), "ko")
  );
  const offerCount = options.reduce((total, option) => {
    const explicit = numeric(option.offer_count);
    if (explicit !== null && explicit >= 0) return total + explicit;
    const inferred = option.provider_offers?.length || option.agencies?.length || 1;
    return total + inferred;
  }, 0);
  const crossAgencyDuplicateCount = options.filter((option) => {
    if (option.cross_agency_duplicate === true) return true;
    const agencies = option.provider_offers?.length
      ? option.provider_offers.map((offer) => offer.agency)
      : option.agencies || [];
    return new Set(agencies.filter(Boolean)).size > 1;
  }).length;
  const daytime = options
    .filter((option) => option.daytime === true)
    .map((option) => numeric(option.actual_hourly_won))
    .filter((value) => value !== null);
  return {
    ...hotel,
    actual_hourly_max: actual.length ? Math.max(...actual) : null,
    actual_hourly_min: actual.length ? Math.min(...actual) : null,
    weekly_allowance_hourly_max: weekly.length ? Math.max(...weekly) : null,
    daytime_actual_hourly_max: daytime.length ? Math.max(...daytime) : null,
    best_commute_adjusted_hourly_won: adjusted.length ? Math.max(...adjusted) : null,
    best_estimated_shift_earnings_won: earnings.length ? Math.max(...earnings) : null,
    actual_shift_total_max: actualTotals.length ? Math.max(...actualTotals) : null,
    weekly_allowance_shift_total_max: weeklyTotals.length ? Math.max(...weeklyTotals) : null,
    shift_total_includes_scheduled_estimates: options.some((option) =>
      !numeric(option.estimated_shift_earnings_won) &&
      scheduledShiftTotal(option.actual_hourly_won, option.duration_minutes ?? shiftDurationMinutes(option.shift)) !== null
    ),
    dates,
    date_count: dates.length,
    shift_count: shifts.length,
    job_count: options.length,
    offer_count: offerCount,
    cross_agency_duplicate_count: crossAgencyDuplicateCount,
    roles: [...new Set(options.map((option) => option.role_category).filter(Boolean))].sort(),
    providers,
    source_snapshot_stale: options.some((option) => option.source_snapshot_stale === true),
    late_return_option_count: options.filter((option) => option.late_return_risk === true).length,
    pay_review_option_count: options.filter((option) => option.pay_confidence !== "high").length,
    filtered_job_options: options,
  };
}

function addFrontiers(hotels) {
  const comparable = hotels.map((hotel) => ({
    ...hotel,
    pareto_actual_pay: Number(hotel.actual_hourly_max) || null,
    pareto_weekly_allowance_pay: Number(hotel.weekly_allowance_hourly_max) || Number(hotel.actual_hourly_max) || null,
    pareto_actual_total_pay: Number(hotel.actual_shift_total_max) || null,
    pareto_weekly_allowance_total_pay: Number(hotel.weekly_allowance_shift_total_max) || Number(hotel.actual_shift_total_max) || null,
  }));
  const actual = paretoHotelIds(comparable, "actual");
  const weekly = paretoHotelIds(comparable, "weekly_allowance");
  const actualTotal = paretoHotelIds(comparable, "actual", "shift_total");
  const weeklyTotal = paretoHotelIds(comparable, "weekly_allowance", "shift_total");
  return comparable.map((hotel) => ({
    ...hotel,
    pareto_actual: actual.has(String(hotel.hotel_id)),
    pareto_weekly_allowance: weekly.has(String(hotel.hotel_id)),
    pareto_actual_total: actualTotal.has(String(hotel.hotel_id)),
    pareto_weekly_allowance_total: weeklyTotal.has(String(hotel.hotel_id)),
    weekly_allowance_rate_stated: Number(hotel.weekly_allowance_hourly_max) > 0,
  }));
}

export function filterHotels(hotels, filters = {}) {
  const maxCommute = numeric(filters.maxCommute);
  const minimumPay = numeric(filters.minimumPay) || 0;
  const query = text(filters.query).toLocaleLowerCase("ko");
  const filtered = [];
  for (const hotel of hotels || []) {
    const commute = numeric(hotel.transit_minutes);
    if (maxCommute !== null && (commute === null || commute > maxCommute)) continue;
    const options = normalizedOptions(hotel).filter((option) => optionMatches(option, filters));
    if (!options.length) continue;
    const derived = deriveHotel(hotel, options);
    if (query) {
      const haystack = [
        derived.employer,
        derived.area,
        derived.station,
        ...derived.roles,
        ...derived.filtered_job_options.map((option) => option.title),
        ...derived.providers.flatMap((provider) => [provider.label, provider.agency]),
      ].map(text).join(" ").toLocaleLowerCase("ko");
      if (!haystack.includes(query)) continue;
    }
    const comparable = filters.payMode === "weekly_allowance"
      ? Number(derived.weekly_allowance_hourly_max) || Number(derived.actual_hourly_max)
      : Number(derived.actual_hourly_max);
    if (!Number.isFinite(comparable) || comparable < minimumPay) continue;
    filtered.push(derived);
  }
  return addFrontiers(filtered);
}

export function hotelDecisionSummary(hotel) {
  const options = hotel?.filtered_job_options || hotel?.job_options || [];
  const best = [...options].sort((left, right) =>
    (Number(right.commute_adjusted_hourly_won) || 0) - (Number(left.commute_adjusted_hourly_won) || 0) ||
    (Number(right.actual_hourly_won) || 0) - (Number(left.actual_hourly_won) || 0)
  )[0] || null;
  return {
    best,
    adjustedHourly: Number(best?.commute_adjusted_hourly_won) || null,
    estimatedEarnings: Number(best?.estimated_shift_earnings_won) || null,
    totalMinutes: Number(best?.total_time_commitment_minutes) || null,
    hasLateReturnRisk: options.some((option) => option.late_return_risk === true),
    reviewPayCount: options.filter((option) => option.pay_confidence !== "high").length,
  };
}

export function serializeHotelFilters(filters) {
  const result = {};
  for (const [key, value] of Object.entries(filters || {})) {
    if (value !== "" && value !== null && value !== undefined && value !== "all") {
      result[key] = String(value);
    }
  }
  return result;
}
