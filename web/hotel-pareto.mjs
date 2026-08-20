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

export const HOTEL_PROVIDER_COLORS = Object.freeze({
  withyou: "#2878b5",
  huplux: "#7657b8",
  eumplus: "#d35f59",
  eoullim: "#c78a14",
  rutine: "#188772",
});

const FALLBACK_PROVIDER_COLORS = Object.freeze([
  "#426b9a",
  "#a6578a",
  "#617d2d",
  "#b65c2e",
  "#3d7f89",
  "#7a6250",
]);

function positiveNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

function verifiedCommuteMinutes(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

function validCoordinate(value, minimum, maximum) {
  if (value === null || value === undefined || value === "") return false;
  const number = Number(value);
  return Number.isFinite(number) && number >= minimum && number <= maximum;
}

export function hotelLocationLabel(hotel) {
  const statedLocation = compactText(hotel?.station || hotel?.area);
  if (statedLocation) return statedLocation;
  const hasMappedWorksite = validCoordinate(hotel?.latitude, -90, 90) &&
    validCoordinate(hotel?.longitude, -180, 180);
  return hasMappedWorksite ? "Mapped worksite" : "Location unavailable";
}

export function hotelProviderColor(agency) {
  const key = compactText(agency).toLocaleLowerCase("en");
  if (HOTEL_PROVIDER_COLORS[key]) return HOTEL_PROVIDER_COLORS[key];
  let hash = 0;
  for (const character of key) hash = ((hash << 5) - hash + character.codePointAt(0)) | 0;
  return FALLBACK_PROVIDER_COLORS[Math.abs(hash) % FALLBACK_PROVIDER_COLORS.length];
}

export function hotelProviderComparablePay(provider, mode = "actual") {
  if (!HOTEL_PAY_MODES[mode]) throw new Error(`Unsupported hotel pay mode: ${mode}`);
  const actualPay = positiveNumber(provider?.actual_hourly_max);
  const weeklyPay = positiveNumber(provider?.weekly_allowance_hourly_max);
  return mode === "weekly_allowance" ? weeklyPay || actualPay : actualPay;
}

export function hotelProviderComparisons(hotel, mode = "actual") {
  const comparisons = new Map();
  for (const provider of hotel?.providers || []) {
    const agency = compactText(provider.agency || provider.label);
    if (!agency) continue;
    const actualPay = positiveNumber(provider.actual_hourly_max);
    const weeklyPay = positiveNumber(provider.weekly_allowance_hourly_max);
    const current = comparisons.get(agency) || {
      agency,
      label: compactText(provider.label || agency) || agency,
      actualPay: null,
      weeklyPay: null,
      offerCount: 0,
      color: hotelProviderColor(agency),
    };
    current.actualPay = Math.max(current.actualPay || 0, actualPay || 0) || null;
    current.weeklyPay = Math.max(current.weeklyPay || 0, weeklyPay || 0) || null;
    current.offerCount += Number(provider.offer_count) || 0;
    comparisons.set(agency, current);
  }
  const normalized = [...comparisons.values()].map((provider) => ({
    ...provider,
    comparablePay: mode === "weekly_allowance"
      ? provider.weeklyPay || provider.actualPay
      : provider.actualPay,
    usesActualFallback: mode === "weekly_allowance" && !provider.weeklyPay,
  })).filter((provider) => provider.comparablePay);
  return normalized.sort((left, right) =>
    right.comparablePay - left.comparablePay ||
    left.label.localeCompare(right.label, "ko")
  );
}

export function mergedHotelProviderComparisons(hotels, mode = "actual") {
  const merged = new Map();
  for (const hotel of hotels || []) {
    for (const provider of hotelProviderComparisons(hotel, mode)) {
      const current = merged.get(provider.agency) || {
        ...provider,
        hotelCount: 0,
        offerCount: 0,
      };
      current.hotelCount += 1;
      current.offerCount += provider.offerCount;
      current.actualPay = Math.max(current.actualPay || 0, provider.actualPay || 0) || null;
      current.weeklyPay = Math.max(current.weeklyPay || 0, provider.weeklyPay || 0) || null;
      if (provider.comparablePay > current.comparablePay) {
        current.comparablePay = provider.comparablePay;
        current.usesActualFallback = provider.usesActualFallback;
      }
      merged.set(provider.agency, current);
    }
  }
  return [...merged.values()].sort((left, right) =>
    right.comparablePay - left.comparablePay ||
    left.label.localeCompare(right.label, "ko")
  );
}

export function providerConicGradient(providers) {
  const colors = (providers || []).map((provider) => provider.color).filter(Boolean);
  if (!colors.length) return "#73847d";
  if (colors.length === 1) return colors[0];
  const slice = 360 / colors.length;
  const angle = (value) => Number(value.toFixed(2));
  return `conic-gradient(${colors.map((color, index) =>
    `${color} ${angle(index * slice)}deg ${angle((index + 1) * slice)}deg`
  ).join(", ")})`;
}

export function hotelProviderBreakdown(points) {
  const providers = new Map();
  for (const point of points || []) {
    const provider = point?.provider;
    const agency = compactText(provider?.agency || provider?.label);
    if (!agency) continue;
    const current = providers.get(agency) || {
      agency,
      label: compactText(provider?.label || agency) || agency,
      color: provider?.color || hotelProviderColor(agency),
      pointCount: 0,
      bestPay: null,
    };
    current.pointCount += 1;
    current.bestPay = Math.max(current.bestPay || 0, positiveNumber(point?.pay) || 0) || null;
    providers.set(agency, current);
  }
  return [...providers.values()].sort((left, right) =>
    (right.bestPay || 0) - (left.bestPay || 0) ||
    left.label.localeCompare(right.label, "ko")
  );
}

export function hotelPointProviderBreakdown(points, mode = "actual") {
  const providerPoints = (points || []).flatMap((point) => {
    if (point?.provider) return [point];
    return hotelProviderComparisons(point?.hotel, mode).map((provider) => ({
      provider,
      pay: provider.comparablePay,
    }));
  });
  return hotelProviderBreakdown(providerPoints);
}

export function providerBreakdownConicGradient(breakdown) {
  return providerConicGradient((breakdown || []).flatMap((provider) =>
    Array.from({ length: Math.max(0, Number(provider.pointCount) || 0) }, () => provider)
  ));
}

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
    commute: verifiedCommuteMinutes(hotel?.transit_minutes),
  })).filter((item) => item.id && item.pay && item.commute !== null);
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
    .filter((point) => point.pay && point.commute !== null);
}

export function hotelProviderParetoPoints(hotels, mode = "actual") {
  const points = (hotels || []).flatMap((hotel) =>
    hotelProviderComparisons(hotel, mode).map((provider) => ({
      hotel,
      provider,
      pay: provider.comparablePay,
      commute: verifiedCommuteMinutes(hotel?.transit_minutes),
      frontier: false,
    }))
  ).filter((point) => point.pay && point.commute !== null);
  return points.map((point) => ({
    ...point,
    frontier: !points.some((other) =>
      hotelPointId(other) !== hotelPointId(point) &&
      other.pay >= point.pay &&
      other.commute <= point.commute &&
      (other.pay > point.pay || other.commute < point.commute)
    ),
  }));
}

export function hotelPointForHotel(hotel, mode = "actual") {
  return {
    hotel,
    pay: hotelComparablePay(hotel, mode),
    commute: verifiedCommuteMinutes(hotel?.transit_minutes),
    frontier: isHotelOnFrontier(hotel, mode),
  };
}

export function hotelPointId(point) {
  const hotelId = compactText(point?.hotel?.hotel_id);
  const providerId = compactText(point?.provider?.agency);
  return providerId ? `provider:${hotelId}:${providerId}` : `hotel:${hotelId}`;
}

export function hotelPointSummary(point) {
  const hotel = point?.hotel || {};
  const provider = point?.provider || null;
  const providers = provider
    ? [compactText(provider.label || provider.agency)]
    : (hotel.providers || [])
      .map((item) => compactText(item.label || item.agency))
      .filter(Boolean);
  const summary = {
    hotelId: compactText(hotel.hotel_id),
    employer: compactText(hotel.employer) || "Unknown hotel",
    area: hotelLocationLabel(hotel),
    pay: Number(point?.pay) || 0,
    commuteLabel: Number.isFinite(point?.commute) ? `${point.commute} min` : "Unverified",
    optionLabel: `${Number(hotel.date_count) || 0} days · ${Number(hotel.shift_count) || 0} times`,
    providerLabel: providers.join(" · ") || "Provider unavailable",
    frontier: point?.frontier === true,
  };
  if (!provider) return summary;
  return {
    ...summary,
    pointId: hotelPointId(point),
    providerId: compactText(provider.agency),
    providerColor: provider.color,
    providerUsesActualFallback: provider.usesActualFallback === true,
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
