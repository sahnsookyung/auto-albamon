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
