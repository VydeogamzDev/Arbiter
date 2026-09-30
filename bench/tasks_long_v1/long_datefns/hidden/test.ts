import { describe, expect, it } from "vitest";
import { closestIndexTo, closestTo, differenceInBusinessDays, eachWeekendOfInterval, formatDuration, isWeekend, nextDay, nextMonday, nextSunday, previousDay, previousFriday, roundToNearestMinutes, roundToNearestSeconds } from "../index.js";

// ---- js_closest_direction
{
const d = (day: number) => new Date(2024, 0, day);
const dates = [d(5), d(12), d(20)];

describe("direction", () => {
  it("index_default", () => {
    expect(closestIndexTo(d(10), dates)).toBe(1);
  });
  it("index_before", () => {
    expect(closestIndexTo(d(10), dates, { direction: "before" })).toBe(0);
    expect(closestIndexTo(d(12), dates, { direction: "before" })).toBe(1);
  });
  it("index_after", () => {
    expect(closestIndexTo(d(13), dates, { direction: "after" })).toBe(2);
  });
  it("closest_direction", () => {
    expect(closestTo(d(10), dates, { direction: "before" })).toEqual(d(5));
    expect(closestTo(d(13), dates, { direction: "after" })).toEqual(d(20));
    expect(closestTo(d(13), dates)).toEqual(d(12));
  });
  it("none_qualifies", () => {
    expect(closestIndexTo(d(1), dates, { direction: "before" })).toBeUndefined();
    expect(closestTo(d(25), dates, { direction: "after" })).toBeUndefined();
  });
});
}

// ---- js_duration_units
{
describe("formatDuration options", () => {
  it("max_units", () => {
    expect(formatDuration({ years: 2, months: 9, weeks: 1, days: 7 }, { maxUnits: 2 })).toBe("2 years 9 months");
  });
  it("max_units_zero", () => {
    expect(formatDuration({ years: 0, months: 1, days: 3 },
      { format: ["years", "months", "days"], zero: true, maxUnits: 2 })).toBe("0 years 1 month");
  });
  it("last_delimiter", () => {
    expect(formatDuration({ years: 2, months: 9, days: 3 }, { lastDelimiter: " and " }))
      .toBe("2 years 9 months and 3 days");
    expect(formatDuration({ years: 2, months: 9, days: 3 }, { delimiter: ", ", lastDelimiter: " and " }))
      .toBe("2 years, 9 months and 3 days");
  });
  it("single_unit", () => {
    expect(formatDuration({ days: 3 }, { lastDelimiter: " and " })).toBe("3 days");
  });
  it("zero_text", () => {
    expect(formatDuration({}, { zeroText: "now" })).toBe("now");
    expect(formatDuration({ seconds: 0 }, { zeroText: "now" })).toBe("now");
  });
  it("default_unchanged", () => {
    expect(formatDuration({})).toBe("");
    expect(formatDuration({ years: 2, days: 3 })).toBe("2 years 3 days");
  });
});
}

// ---- js_round_seconds
{
const at = (s: number, ms: number) => new Date(2024, 0, 1, 12, 0, s, ms);

describe("roundToNearestSeconds", () => {
  it("rounds", () => {
    expect(roundToNearestSeconds(at(10, 600))).toEqual(at(11, 0));
    expect(roundToNearestSeconds(at(10, 400))).toEqual(at(10, 0));
  });
  it("rounding_method", () => {
    expect(roundToNearestSeconds(at(10, 600), { roundingMethod: "floor" })).toEqual(at(10, 0));
    expect(roundToNearestSeconds(at(10, 100), { roundingMethod: "ceil" })).toEqual(at(11, 0));
  });
  it("nearest_to", () => {
    expect(roundToNearestSeconds(at(10, 600), { nearestTo: 15 })).toEqual(at(15, 0));
  });
  it("invalid_range", () => {
    expect(isNaN(+roundToNearestSeconds(at(10, 0), { nearestTo: 0 }))).toBe(true);
    expect(isNaN(+roundToNearestSeconds(at(10, 0), { nearestTo: 60 } as never))).toBe(true);
  });
  it("wide_range", () => {
    expect(roundToNearestSeconds(at(40, 0), { nearestTo: 45 } as never)).toEqual(at(45, 0));
  });
  it("minutes_unchanged", () => {
    expect(isNaN(+roundToNearestMinutes(at(0, 0), { nearestTo: 45 } as never))).toBe(true);
  });
});
}

// ---- js_same_day
{
const d = (m: number, day: number) => new Date(2024, m, day);

describe("includeSameDay", () => {
  it("next_same", () => {
    expect(nextDay(d(0, 1), 1, { includeSameDay: true })).toEqual(d(0, 1));
    expect(nextDay(d(0, 1), 3, { includeSameDay: true })).toEqual(d(0, 3));
  });
  it("next_default", () => {
    expect(nextDay(d(0, 1), 1)).toEqual(d(0, 8));
  });
  it("previous_same", () => {
    expect(previousDay(d(0, 3), 3, { includeSameDay: true })).toEqual(d(0, 3));
    expect(previousDay(d(0, 3), 3)).toEqual(new Date(2023, 11, 27));
  });
  it("helpers", () => {
    expect(nextMonday(d(0, 1), { includeSameDay: true })).toEqual(d(0, 1));
    expect(previousFriday(d(0, 5), { includeSameDay: true })).toEqual(d(0, 5));
    expect(nextSunday(d(0, 7), { includeSameDay: true })).toEqual(d(0, 7));
  });
});
}

// ---- js_weekend_days
{
const d = (day: number) => new Date(2024, 0, day);

describe("weekendDays", () => {
  it("weekend_default", () => {
    expect(isWeekend(d(6))).toBe(true);
    expect(isWeekend(d(5))).toBe(false);
  });
  it("weekend_option", () => {
    expect(isWeekend(d(5), { weekendDays: [5, 6] })).toBe(true);
    expect(isWeekend(d(7), { weekendDays: [5, 6] })).toBe(false);
  });
  it("each_weekend_option", () => {
    const r = eachWeekendOfInterval({ start: d(1), end: d(14) }, { weekendDays: [5, 6] });
    expect(r.map((x) => x.getDate())).toEqual([5, 6, 12, 13]);
  });
  it("each_weekend_default", () => {
    const r = eachWeekendOfInterval({ start: d(1), end: d(14) });
    expect(r.map((x) => x.getDate())).toEqual([6, 7, 13, 14]);
  });
  it("business_days_option", () => {
    expect(differenceInBusinessDays(d(6), d(1), { weekendDays: [5, 6] })).toBe(4);
    expect(differenceInBusinessDays(d(22), d(1), { weekendDays: [5, 6] })).toBe(15);
  });
  it("business_days_default", () => {
    expect(differenceInBusinessDays(d(6), d(1))).toBe(5);
    expect(differenceInBusinessDays(d(22), d(1))).toBe(15);
  });
});
}
