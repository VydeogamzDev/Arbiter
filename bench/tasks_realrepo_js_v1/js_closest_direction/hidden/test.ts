import { describe, expect, it } from "vitest";
import { closestIndexTo, closestTo } from "../index.js";

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
