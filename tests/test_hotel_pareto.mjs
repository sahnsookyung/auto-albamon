import assert from "node:assert/strict";
import test from "node:test";

import {
  hotelComparablePay,
  hotelLocationLabel,
  hotelParetoPoints,
  hotelPointForHotel,
  hotelPointId,
  hotelPointProviderBreakdown,
  hotelPointSummary,
  hotelProviderBreakdown,
  hotelProviderComparisons,
  hotelProviderParetoPoints,
  hotelRows,
  isHotelOnFrontier,
  mergedHotelProviderComparisons,
  paretoHotelIds,
  providerConicGradient,
  providerBreakdownConicGradient,
} from "../web/hotel-pareto.mjs";

const hotels = [
  {
    employer: "Far",
    actual_hourly_max: 15000,
    pareto_actual_pay: 15000,
    pareto_weekly_allowance_pay: 15000,
    pareto_actual_total_pay: 120000,
    pareto_weekly_allowance_total_pay: 120000,
    transit_minutes: 50,
    pareto_actual: true,
    pareto_weekly_allowance: false,
    pareto_actual_total: true,
    pareto_weekly_allowance_total: false,
  },
  {
    employer: "Weekly",
    actual_hourly_max: 14000,
    weekly_allowance_hourly_max: 16800,
    pareto_actual_pay: 14000,
    pareto_weekly_allowance_pay: 16800,
    pareto_actual_total_pay: 112000,
    pareto_weekly_allowance_total_pay: 134400,
    transit_minutes: 30,
    pareto_actual: true,
    pareto_weekly_allowance: true,
    pareto_actual_total: true,
    pareto_weekly_allowance_total: true,
  },
  {
    employer: "Near",
    actual_hourly_max: 13200,
    pareto_actual_pay: 13200,
    pareto_weekly_allowance_pay: 13200,
    pareto_actual_total_pay: 52800,
    pareto_weekly_allowance_total_pay: 52800,
    transit_minutes: 18,
    pareto_actual: true,
    pareto_weekly_allowance: true,
    pareto_actual_total: true,
    pareto_weekly_allowance_total: true,
  },
];

test("pay mode changes both comparable value and frontier membership", () => {
  assert.equal(hotelComparablePay(hotels[1], "actual"), 14000);
  assert.equal(hotelComparablePay(hotels[1], "weekly_allowance"), 16800);
  assert.equal(isHotelOnFrontier(hotels[0], "actual"), true);
  assert.equal(isHotelOnFrontier(hotels[0], "weekly_allowance"), false);
});

test("pay metric switches the hotel comparison from hourly rate to shift total", () => {
  assert.equal(hotelComparablePay(hotels[0], "actual", "shift_total"), 120_000);
  assert.equal(hotelComparablePay(hotels[1], "weekly_allowance", "shift_total"), 134_400);
  assert.equal(isHotelOnFrontier(hotels[0], "weekly_allowance", "shift_total"), false);
});

test("frontier rows and all-hotel rows are deterministic", () => {
  assert.deepEqual(
    hotelRows(hotels, { mode: "weekly_allowance" }).map((hotel) => hotel.employer),
    ["Weekly", "Near"],
  );
  assert.deepEqual(
    hotelRows(hotels, { mode: "weekly_allowance", showAll: true }).map((hotel) => hotel.employer),
    ["Weekly", "Far", "Near"],
  );
});

test("interactive hotel points expose hover and detail metadata", () => {
  const hotel = {
    ...hotels[1],
    hotel_id: "weekly-id",
    employer: "  JW   Marriott ",
    station: "고속터미널역",
    date_count: 4,
    shift_count: 3,
    providers: [{ agency: "eumplus", label: "이음플러스" }],
  };
  const [point] = hotelParetoPoints([hotel], "weekly_allowance");

  assert.deepEqual(hotelPointSummary(point), {
    hotelId: "weekly-id",
    employer: "JW Marriott",
    area: "고속터미널역",
    pay: 16800,
    payMetric: "hourly",
    commuteLabel: "30 min",
    optionLabel: "4 days · 3 times",
    providerLabel: "이음플러스",
    frontier: true,
  });
});

test("map point summaries use the selected hotel pay mode", () => {
  const point = hotelPointForHotel({
    ...hotels[1],
    hotel_id: "weekly-map-id",
    employer: "JW Marriott",
    area: "서초구",
    date_count: 4,
    shift_count: 3,
    providers: [{ label: "이음플러스" }],
  }, "weekly_allowance");

  assert.equal(hotelPointSummary(point).pay, 16_800);
  assert.equal(hotelPointSummary(point).commuteLabel, "30 min");
});

test("hotels without a verified commute are excluded from every frontier projection", () => {
  const unknown = {
    hotel_id: "unknown",
    employer: "Unknown commute",
    transit_minutes: null,
    pareto_actual_pay: 50_000,
    pareto_actual: true,
    providers: [{ agency: "withyou", actual_hourly_max: 50_000 }],
  };
  const verified = {
    hotel_id: "verified",
    employer: "Verified commute",
    transit_minutes: 30,
    pareto_actual_pay: 12_000,
    pareto_actual: true,
    providers: [{ agency: "withyou", actual_hourly_max: 12_000 }],
  };

  assert.deepEqual([...paretoHotelIds([unknown, verified], "actual")], ["verified"]);
  assert.deepEqual(hotelParetoPoints([unknown, verified]), [hotelPointForHotel(verified)]);
  assert.equal(hotelProviderParetoPoints([unknown, verified]).length, 1);
  assert.equal(hotelPointForHotel(unknown).commute, null);
  assert.equal(hotelPointSummary(hotelPointForHotel(unknown)).commuteLabel, "Unverified");
});

test("mapped coordinates provide an honest location fallback without a source address", () => {
  assert.equal(hotelLocationLabel({ latitude: 37.5014, longitude: 127.03545 }), "Mapped worksite");
  assert.equal(hotelLocationLabel({ latitude: null, longitude: null }), "Location unavailable");
});

test("provider comparisons preserve separate pay observations and change winner by pay mode", () => {
  const hotel = {
    hotel_id: "shared",
    transit_minutes: 30,
    providers: [
      {
        agency: "huplux",
        label: "후플럭스",
        actual_hourly_max: 15_000,
        weekly_allowance_hourly_max: null,
        offer_count: 2,
      },
      {
        agency: "eumplus",
        label: "이음플러스",
        actual_hourly_max: 14_000,
        weekly_allowance_hourly_max: 16_800,
        offer_count: 3,
      },
    ],
  };

  const actual = hotelProviderComparisons(hotel, "actual");
  const weekly = hotelProviderComparisons(hotel, "weekly_allowance");

  assert.deepEqual(actual.map((provider) => provider.agency), ["huplux", "eumplus"]);
  assert.deepEqual(weekly.map((provider) => provider.agency), ["eumplus", "huplux"]);
  assert.equal(weekly[0].comparablePay, 16_800);
  assert.equal(weekly[1].usesActualFallback, true);
  assert.match(providerConicGradient(actual), /^conic-gradient\(/);
  assert.match(providerConicGradient(actual), /0deg 180deg/);
  assert.match(providerConicGradient(actual), /180deg 360deg/);
});

test("shift-total provider comparisons mark an actual-pay fallback when no weekly total is comparable", () => {
  const [provider] = hotelProviderComparisons({
    providers: [{
      agency: "withyou",
      label: "위드유",
      actual_hourly_max: 13_000,
      weekly_allowance_hourly_max: 15_600,
      actual_shift_total_max: 104_000,
      weekly_allowance_shift_total_max: null,
    }],
  }, "weekly_allowance", "shift_total");

  assert.equal(provider.comparablePay, 104_000);
  assert.equal(provider.usesActualFallback, true);
  assert.equal(provider.payMetric, "shift_total");
});

test("provider Pareto mode emits one independently ranked point per hotel and source", () => {
  const shared = {
    hotel_id: "shared",
    employer: "Shared Hotel",
    transit_minutes: 30,
    providers: [
      { agency: "huplux", label: "후플럭스", actual_hourly_max: 15_000 },
      { agency: "eumplus", label: "이음플러스", actual_hourly_max: 14_000 },
    ],
  };
  const near = {
    hotel_id: "near",
    employer: "Near Hotel",
    transit_minutes: 15,
    providers: [
      { agency: "withyou", label: "위드유", actual_hourly_max: 13_000 },
    ],
  };

  const points = hotelProviderParetoPoints([shared, near], "actual");
  assert.equal(points.length, 3);
  assert.equal(points.find((point) => point.provider.agency === "eumplus").frontier, false);
  assert.equal(points.find((point) => point.provider.agency === "huplux").frontier, true);
  assert.equal(points.find((point) => point.provider.agency === "withyou").frontier, true);
  assert.equal(hotelPointId(points[0]).startsWith("provider:shared:"), true);
  assert.equal(hotelPointSummary(points[0]).providerId.length > 0, true);
});

test("visible provider legend aggregation keeps each source once", () => {
  const merged = mergedHotelProviderComparisons([
    {
      providers: [
        { agency: "huplux", label: "후플럭스", actual_hourly_max: 15_000, offer_count: 2 },
        { agency: "eumplus", label: "이음플러스", actual_hourly_max: 14_000, offer_count: 1 },
      ],
    },
    {
      providers: [
        { agency: "huplux", label: "후플럭스", actual_hourly_max: 13_000, offer_count: 4 },
      ],
    },
  ], "actual");

  assert.deepEqual(merged.map((provider) => provider.agency), ["huplux", "eumplus"]);
  assert.equal(merged[0].comparablePay, 15_000);
  assert.equal(merged[0].actualPay, 15_000);
  assert.equal(merged[0].hotelCount, 2);
  assert.equal(merged[0].offerCount, 6);
});

test("overlap breakdown preserves provider counts and weights the color ring", () => {
  const points = [
    { pay: 14_000, provider: { agency: "eumplus", label: "이음플러스", color: "#d35f59" } },
    { pay: 16_800, provider: { agency: "eumplus", label: "이음플러스", color: "#d35f59" } },
    { pay: 15_000, provider: { agency: "huplux", label: "후플럭스", color: "#7657b8" } },
    { pay: 14_400, provider: { agency: "withyou", label: "위드유", color: "#2878b5" } },
  ];

  const breakdown = hotelProviderBreakdown(points);

  assert.deepEqual(
    breakdown.map(({ agency, pointCount, bestPay }) => ({ agency, pointCount, bestPay })),
    [
      { agency: "eumplus", pointCount: 2, bestPay: 16_800 },
      { agency: "huplux", pointCount: 1, bestPay: 15_000 },
      { agency: "withyou", pointCount: 1, bestPay: 14_400 },
    ],
  );
  assert.equal(
    providerBreakdownConicGradient(breakdown),
    "conic-gradient(#d35f59 0deg 90deg, #d35f59 90deg 180deg, #7657b8 180deg 270deg, #2878b5 270deg 360deg)",
  );
});

test("hotel-level overlap breakdown includes every source represented by each hotel", () => {
  const points = hotelParetoPoints([
    {
      hotel_id: "one",
      pareto_actual_pay: 15_000,
      pareto_actual: true,
      transit_minutes: 30,
      providers: [
        { agency: "huplux", label: "후플럭스", actual_hourly_max: 15_000 },
        { agency: "withyou", label: "위드유", actual_hourly_max: 13_000 },
      ],
    },
    {
      hotel_id: "two",
      pareto_actual_pay: 14_000,
      pareto_actual: false,
      transit_minutes: 31,
      providers: [
        { agency: "huplux", label: "후플럭스", actual_hourly_max: 14_000 },
        { agency: "eumplus", label: "이음플러스", actual_hourly_max: 14_000 },
      ],
    },
  ]);

  assert.deepEqual(
    hotelPointProviderBreakdown(points, "actual").map(({ agency, pointCount }) => ({ agency, pointCount })),
    [
      { agency: "huplux", pointCount: 2 },
      { agency: "eumplus", pointCount: 1 },
      { agency: "withyou", pointCount: 1 },
    ],
  );
});
