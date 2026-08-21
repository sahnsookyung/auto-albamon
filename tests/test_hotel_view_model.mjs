import assert from "node:assert/strict";
import test from "node:test";

import {
  filterHotels,
  hotelDecisionSummary,
  shiftFitsWindow,
} from "../web/hotel-view-model.mjs";

const hotels = [
  {
    hotel_id: "near",
    employer: "Near Hotel",
    transit_minutes: 20,
    providers: [
      { agency: "withyou", label: "위드유" },
      { agency: "eumplus", label: "이음플러스" },
      { agency: "huplux", label: "후플럭스" },
    ],
    offer_count: 20,
    cross_agency_duplicate_count: 5,
    job_options: [
      {
        option_id: "near-day",
        work_date: "2026-08-29",
        shift: "10:00~18:00",
        role_category: "guest_service",
        agencies: ["withyou"],
        best_agencies: ["withyou"],
        provider_offers: [{ agency: "withyou", actual_hourly_won: 13_000 }],
        offer_count: 1,
        cross_agency_duplicate: false,
        actual_hourly_won: 13_000,
        weekly_allowance_hourly_won: 15_600,
        estimated_shift_earnings_won: 104_000,
        commute_adjusted_hourly_won: 12_000,
        daytime: true,
        late_return_risk: false,
        pay_confidence: "high",
      },
      {
        option_id: "near-night",
        work_date: "2026-08-30",
        shift: "20:00~02:00",
        role_category: "kitchen_assistant",
        agencies: ["eumplus", "huplux"],
        best_agencies: ["eumplus"],
        provider_offers: [
          { agency: "eumplus", actual_hourly_won: 15_000 },
          { agency: "huplux", actual_hourly_won: 14_000 },
        ],
        offer_count: 2,
        cross_agency_duplicate: true,
        actual_hourly_won: 15_000,
        weekly_allowance_hourly_won: null,
        estimated_shift_earnings_won: 90_000,
        commute_adjusted_hourly_won: 13_235,
        daytime: false,
        late_return_risk: true,
        pay_confidence: "derived",
      },
    ],
  },
  {
    hotel_id: "far",
    employer: "Far Hotel",
    transit_minutes: 65,
    providers: [{ agency: "huplux", label: "후플럭스" }],
    job_options: [{
      option_id: "far-day",
      work_date: "2026-08-29",
      shift: "09:00~18:00",
      role_category: "guest_service",
      agencies: ["huplux"],
      actual_hourly_won: 18_000,
      estimated_shift_earnings_won: 162_000,
      commute_adjusted_hourly_won: 14_294,
      pay_confidence: "high",
    }],
  },
];

test("overnight shift windows compare across midnight", () => {
  assert.equal(shiftFitsWindow("20:00~02:00", "19:00", "02:00"), true);
  assert.equal(shiftFitsWindow("18:00~02:00", "19:00", "02:00"), false);
  assert.equal(shiftFitsWindow("20:00~03:00", "19:00", "02:00"), false);
});

test("date, shift, commute, role and provider filters apply to exact options", () => {
  const result = filterHotels(hotels, {
    dateStart: "2026-08-29",
    dateEnd: "2026-08-29",
    shiftStart: "09:00",
    shiftEnd: "19:00",
    maxCommute: "30",
    minimumPay: "12000",
    role: "guest_service",
    provider: "withyou",
    payMode: "actual",
  });

  assert.equal(result.length, 1);
  assert.equal(result[0].hotel_id, "near");
  assert.equal(result[0].job_count, 1);
  assert.deepEqual(result[0].dates, ["2026-08-29"]);
  assert.equal(result[0].actual_hourly_max, 13_000);
  assert.equal(result[0].daytime_actual_hourly_max, 13_000);
  assert.equal(result[0].offer_count, 1);
  assert.equal(result[0].cross_agency_duplicate_count, 0);
  assert.deepEqual(result[0].providers.map((provider) => provider.agency), ["withyou"]);
  assert.equal(result[0].providers[0].actual_hourly_max, 13_000);
  assert.equal(result[0].actual_shift_total_max, 104_000);
  assert.equal(result[0].weekly_allowance_shift_total_max, 124_800);
  assert.equal(result[0].providers[0].actual_shift_total_max, 104_000);
});

test("text search only considers options left by the other filters", () => {
  const hiddenProvider = filterHotels(hotels, {
    query: "위드유",
    dateStart: "2026-08-30",
    dateEnd: "2026-08-30",
    role: "all",
    provider: "all",
    payMode: "actual",
  });
  const visibleProvider = filterHotels(hotels, {
    query: "이음플러스",
    dateStart: "2026-08-30",
    dateEnd: "2026-08-30",
    role: "all",
    provider: "all",
    payMode: "actual",
  });

  assert.deepEqual(hiddenProvider, []);
  assert.equal(visibleProvider.length, 1);
  assert.deepEqual(visibleProvider[0].dates, ["2026-08-30"]);
  assert.equal(visibleProvider[0].offer_count, 2);
  assert.equal(visibleProvider[0].cross_agency_duplicate_count, 1);
});

test("filtering recalculates both Pareto frontiers and decision metrics", () => {
  const result = filterHotels(hotels, {
    minimumPay: "12000",
    role: "all",
    provider: "all",
    payMode: "actual",
  });
  const near = result.find((hotel) => hotel.hotel_id === "near");
  const summary = hotelDecisionSummary(near);

  assert.equal(near.pareto_actual, true);
  assert.equal(near.pareto_actual_total, true);
  assert.equal(result.find((hotel) => hotel.hotel_id === "far").pareto_actual, true);
  assert.equal(summary.adjustedHourly, 13_235);
  assert.equal(summary.hasLateReturnRisk, true);
  assert.equal(summary.reviewPayCount, 1);
});
