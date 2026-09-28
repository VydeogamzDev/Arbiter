import { describe, expect, it } from "vitest";
import { differenceInBusinessDays, eachWeekendOfInterval, isWeekend } from "../index.js";

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
