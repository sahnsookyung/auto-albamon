export const HOTEL_PAY_MODES = Object.freeze({
  actual: {
    payField: "pareto_actual_pay",
    frontierField: "pareto_actual",
  },
  weekly_allowance: {
    payField: "pareto_weekly_allowance_pay",
    frontierField: "pareto_weekly_allowance",
  },
});

export function hotelComparablePay(hotel, mode = "actual") {
  const config = HOTEL_PAY_MODES[mode];
  if (!config) throw new Error(`Unsupported hotel pay mode: ${mode}`);
  const value = Number(hotel?.[config.payField]);
  return Number.isFinite(value) && value > 0 ? value : null;
}

export function isHotelOnFrontier(hotel, mode = "actual") {
  const config = HOTEL_PAY_MODES[mode];
  if (!config) throw new Error(`Unsupported hotel pay mode: ${mode}`);
  return hotel?.[config.frontierField] === true;
}

export function paretoHotelIds(hotels, mode = "actual") {
  const candidates = (hotels || []).map((hotel) => ({
    id: String(hotel?.hotel_id || ""),
    pay: hotelComparablePay(hotel, mode),
    commute: Number(hotel?.transit_minutes),
  })).filter((item) => item.id && item.pay && Number.isFinite(item.commute));
  return new Set(candidates.filter((candidate) => !candidates.some((other) =>
    other.id !== candidate.id &&
    other.pay >= candidate.pay &&
    other.commute <= candidate.commute &&
    (other.pay > candidate.pay || other.commute < candidate.commute)
  )).map((item) => item.id));
}

function compactText(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

export function hotelParetoPoints(hotels, mode = "actual") {
  return (hotels || []).map((hotel) => hotelPointForHotel(hotel, mode))
    .filter((point) => point.pay && Number.isFinite(point.commute));
}

export function hotelPointForHotel(hotel, mode = "actual") {
  return {
    hotel,
    pay: hotelComparablePay(hotel, mode),
    commute: Number(hotel?.transit_minutes),
    frontier: isHotelOnFrontier(hotel, mode),
  };
}

export function hotelPointSummary(point) {
  const hotel = point?.hotel || {};
  const providers = (hotel.providers || [])
    .map((provider) => compactText(provider.label || provider.agency))
    .filter(Boolean);
  return {
    hotelId: compactText(hotel.hotel_id),
    employer: compactText(hotel.employer) || "Unknown hotel",
    area: compactText(hotel.station || hotel.area) || "Location unavailable",
    pay: Number(point?.pay) || 0,
    commuteLabel: Number.isFinite(point?.commute) ? `${point.commute} min` : "Unverified",
    optionLabel: `${Number(hotel.date_count) || 0} days · ${Number(hotel.shift_count) || 0} times`,
    providerLabel: providers.join(" · ") || "Provider unavailable",
    frontier: point?.frontier === true,
  };
}

export function hotelRows(hotels, { mode = "actual", showAll = false } = {}) {
  const rows = (hotels || []).filter((hotel) =>
    showAll || isHotelOnFrontier(hotel, mode)
  );
  return [...rows].sort((left, right) =>
    (hotelComparablePay(right, mode) || 0) - (hotelComparablePay(left, mode) || 0) ||
    (Number(left.transit_minutes) || 10_000) - (Number(right.transit_minutes) || 10_000) ||
    String(left.employer || "").localeCompare(String(right.employer || ""), "ko")
  );
}
