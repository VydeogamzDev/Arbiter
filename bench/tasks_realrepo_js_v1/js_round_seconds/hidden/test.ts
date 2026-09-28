import { describe, expect, it } from "vitest";
import { roundToNearestMinutes, roundToNearestSeconds } from "../index.js";

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
