import assert from "node:assert/strict";
import test from "node:test";

import {
  hotelComparablePay,
  hotelParetoPoints,
  hotelPointForHotel,
  hotelPointSummary,
  hotelRows,
  isHotelOnFrontier,
} from "../web/hotel-pareto.mjs";

const hotels = [
  {
    employer: "Far",
    actual_hourly_max: 15000,
    pareto_actual_pay: 15000,
    pareto_weekly_allowance_pay: 15000,
    transit_minutes: 50,
    pareto_actual: true,
    pareto_weekly_allowance: false,
  },
  {
    employer: "Weekly",
    actual_hourly_max: 14000,
    weekly_allowance_hourly_max: 16800,
    pareto_actual_pay: 14000,
    pareto_weekly_allowance_pay: 16800,
    transit_minutes: 30,
    pareto_actual: true,
    pareto_weekly_allowance: true,
  },
  {
    employer: "Near",
    actual_hourly_max: 13200,
    pareto_actual_pay: 13200,
    pareto_weekly_allowance_pay: 13200,
    transit_minutes: 18,
    pareto_actual: true,
    pareto_weekly_allowance: true,
  },
];

test("pay mode changes both comparable value and frontier membership", () => {
  assert.equal(hotelComparablePay(hotels[1], "actual"), 14000);
  assert.equal(hotelComparablePay(hotels[1], "weekly_allowance"), 16800);
  assert.equal(isHotelOnFrontier(hotels[0], "actual"), true);
  assert.equal(isHotelOnFrontier(hotels[0], "weekly_allowance"), false);
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
