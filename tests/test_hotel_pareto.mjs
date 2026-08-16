import assert from "node:assert/strict";
import test from "node:test";

import {
  hotelComparablePay,
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
